"""
CouchDB access, connected on first use rather than at import.

couchdb-python makes a real HTTP request inside `server[name]`, so resolving the
databases at module level meant importing this file opened a socket. With
CouchDB down that raised while Python was still importing app.main — uvicorn
never reached the point of binding a port, and the whole backend was
unreachable: auth, /docs and the Atlas mirror included, none of which need
CouchDB at all.

Connecting lazily keeps that failure local. The server starts, everything that
does not touch CouchDB works, and the telemetry endpoints answer 503 until it
comes up — at which point the next request connects and they resume on their
own, with no restart.
"""

from __future__ import annotations

import os
import threading
import time

from couchdb import Server
from couchdb.http import ResourceConflict, ResourceNotFound, ServerError, Unauthorized
from dotenv import load_dotenv

load_dotenv()

COUCHDB_URL = os.getenv("COUCHDB_URL")
RAW_DATABASE = os.getenv("COUCHDB_RAW_DATABASE")
DENOISED_DATABASE = os.getenv("COUCHDB_DENOISED_DATABASE")

# The node registry is small (one document per node, overwritten in place) and
# has nothing to do with the telemetry history, so it gets its own database.
NODES_DATABASE = os.getenv("COUCHDB_NODES_DATABASE", "skypulse-nodes")

NODE_DOC_PREFIX = "node::"


class CouchDBUnavailable(Exception):
    """CouchDB could not be reached or opened. main.py turns this into a 503."""


# Ingest runs on a threadpool, so several packets can arrive at the connect
# path at once; the lock keeps that to a single connection attempt.
_lock = threading.RLock()

_raw_db = None
_denoised_db = None
_nodes_db = None
_indexes_ready = False

# While CouchDB is down every request would otherwise retry the connection. At
# the ~7 packets/second the nodes emit that is pure noise, so a failure is held
# briefly and replayed instead of re-attempted.
_RETRY_AFTER_SECONDS = 2.0
_last_error: CouchDBUnavailable | None = None
_last_error_at = 0.0


def _safe_url() -> str:
    """
    COUCHDB_URL carries the password. Error messages and logs get the host only
    — a stack trace in a terminal must not be a way to read the credentials.
    """

    if not COUCHDB_URL:
        return "(COUCHDB_URL is not set)"

    if "@" in COUCHDB_URL:
        scheme, _, rest = COUCHDB_URL.partition("://")
        return f"{scheme}://***@{rest.split('@', 1)[1]}"

    return COUCHDB_URL


def _open_databases() -> tuple:
    server = Server(COUCHDB_URL)

    # Resolved into locals first: a failure on the second database must not
    # leave the module holding a half-connected first one.
    raw = server[RAW_DATABASE]
    denoised = server[DENOISED_DATABASE]

    try:
        nodes = server[NODES_DATABASE]
    except ResourceNotFound:
        nodes = server.create(NODES_DATABASE)

    return raw, denoised, nodes


def _ensure_connected() -> None:
    global _raw_db, _denoised_db, _nodes_db, _indexes_ready
    global _last_error, _last_error_at

    if _raw_db is not None:
        return

    with _lock:
        # Another thread may have connected while this one waited for the lock.
        if _raw_db is not None:
            return

        if _last_error is not None and time.monotonic() - _last_error_at < _RETRY_AFTER_SECONDS:
            raise _last_error

        if not COUCHDB_URL:
            raise CouchDBUnavailable("COUCHDB_URL is not set in backend/.env.")

        try:
            _raw_db, _denoised_db, _nodes_db = _open_databases()
        except ResourceNotFound:
            error = CouchDBUnavailable(
                f"A CouchDB database is missing at {_safe_url()}. Expected "
                f"'{RAW_DATABASE}' and '{DENOISED_DATABASE}' to exist."
            )
        except Unauthorized:
            error = CouchDBUnavailable(
                f"CouchDB refused the credentials in COUCHDB_URL ({_safe_url()})."
            )
        except OSError as e:
            # ConnectionRefusedError and DNS failures both land here — this is
            # the "CouchDB is not running" case.
            error = CouchDBUnavailable(
                f"Could not reach CouchDB at {_safe_url()}: {e}. "
                "Start the Apache CouchDB service and the next request will connect."
            )
        else:
            _last_error = None
            if not _indexes_ready:
                try:
                    ensure_indexes()
                    _indexes_ready = True
                except Exception as e:
                    # Indexes are an optimisation; a failure here must not stop
                    # the backend from storing telemetry.
                    print("Could not create CouchDB indexes:", e)
            return

        _last_error = error
        _last_error_at = time.monotonic()
        raise error


def get_raw_db():
    _ensure_connected()
    return _raw_db


def get_denoised_db():
    _ensure_connected()
    return _denoised_db


def get_nodes_db():
    _ensure_connected()
    return _nodes_db


def is_connected() -> bool:
    """Whether CouchDB is usable right now, without raising into the caller."""

    try:
        _ensure_connected()
        return True
    except CouchDBUnavailable:
        return False


def ensure_indexes():
    # The dashboard queries by event_type and sorts by timestamp; Mango
    # requires a matching index to run that query without a full scan.
    index = {
        "index": {"fields": ["event_type", "timestamp"]},
        "name": "event_type_timestamp_idx",
    }
    for db in (_raw_db, _denoised_db):
        db.resource.post_json("_index", body=index)

    # get_sensor_history() runs on every ingested packet, so it has to be an
    # indexed lookup: an unindexed scan of the raw db costs ~65ms a call, which
    # is more than the nodes leave between packets.
    history_index = {
        "index": {"fields": ["device_id", "event_type", "timestamp"]},
        "name": "device_event_timestamp_idx",
    }
    _raw_db.resource.post_json("_index", body=history_index)

    _ensure_history_view()


# ---------------------------------------------------------------------------
# Historical browsing
#
# The dashboard's live chart reads the newest N documents, which Mango handles
# fine. Browsing a month does not work that way: the denoised database holds
# ~820k documents, and pulling a range out to average it in Python costs
# seconds and gets worse every day the nodes run.
#
# So the buckets are precomputed. This view emits one row per reading keyed by
# [sensor_id, year, month, day, hour, minute] and reduces with _stats, which
# means CouchDB keeps min/max/sum/count for every minute in the b-tree and can
# roll them up to hours or days by changing group_level alone. A month of
# per-day buckets costs ~20ms and a busy day of per-minute buckets ~170ms,
# whatever the database grows to.
#
# The timestamp is sliced as a string rather than parsed with `new Date()`:
# every timestamp we store is ISO-8601 UTC, string slicing is much faster over
# a million documents, and it cannot pick up a timezone from the server.
# ---------------------------------------------------------------------------

HISTORY_DESIGN_DOC = "_design/history"
HISTORY_VIEW = "history/by_minute"

# Appended to a sensor id for the pre-denoising series. Every reading is
# emitted twice — once filtered, once raw — so the dashboard can lay the noise
# the filters removed over the curve without a second view or a second query
# shape. `_stats` reduces one number at a time, so two series means two keys.
RAW_SUFFIX = "__raw"

_HISTORY_MAP = """
function (doc) {
  if (!doc.timestamp || !doc.event_type) return;

  var t = doc.timestamp;
  var year = parseInt(t.substr(0, 4), 10);
  var month = parseInt(t.substr(5, 2), 10);
  var day = parseInt(t.substr(8, 2), 10);
  var hour = parseInt(t.substr(11, 2), 10);
  var minute = parseInt(t.substr(14, 2), 10);
  if (!year || isNaN(minute)) return;

  var when = [year, month, day, hour, minute];

  function put(id, value) {
    if (typeof value === 'number' && isFinite(value)) emit([id].concat(when), value);
  }

  // The denoised documents keep the original reading in `value` and the
  // filtered one in `filtered_value`, so both series come from one document.
  function pair(id, sensor) {
    if (!sensor) return;

    var raw = sensor.value;
    var filtered = sensor.filtered_value;
    if (typeof filtered !== 'number') filtered = raw;

    put(id, filtered);
    put(id + '__raw', raw);
  }

  if (doc.event_type === 'environment') {
    pair('temperature', doc.temperature);
    pair('humidity', doc.humidity);
    pair('pressure', doc.pressure);
  } else if (doc.event_type === 'acoustic') {
    pair('acoustic', doc.sound);
  } else if (doc.event_type === 'motion') {
    // Motion is the exception: the denoiser rewrites accel_x/y/z in place and
    // leaves `magnitude` as the raw reading. So the filtered magnitude has to
    // be recomputed from the filtered components — reading `magnitude` for
    // both would draw the same line twice.
    var a = doc.accelerometer;
    if (a) {
      put('motion__raw', a.magnitude);
      if (typeof a.accel_x === 'number' && typeof a.accel_y === 'number' &&
          typeof a.accel_z === 'number') {
        put('motion', Math.sqrt(a.accel_x * a.accel_x + a.accel_y * a.accel_y +
                                a.accel_z * a.accel_z));
      }
    }
  }
}
""".strip()

# How many trailing key elements each granularity keeps. 1 is the sensor id
# alone (totals), 6 is down to the minute.
GROUP_LEVELS = {"year": 2, "month": 3, "day": 4, "hour": 5, "minute": 6}


class HistoryIndexBuilding(Exception):
    """
    The view is still building.

    Only ever happens once per database — CouchDB builds the index on first
    read, and over ~820k documents that takes a few minutes and outlives the
    HTTP request that triggered it. Raised rather than swallowed so the API can
    answer 503 "come back shortly" instead of an empty chart that looks like
    missing data.
    """


def _ensure_history_view() -> None:
    """
    Create or update the history design doc.

    Written only when it differs: saving an unchanged design doc still bumps
    its _rev, and CouchDB throws away and rebuilds the whole index whenever the
    design doc changes. Doing that on every reconnect would mean rebuilding a
    million rows because the backend restarted.
    """

    existing = _denoised_db.get(HISTORY_DESIGN_DOC)

    if existing is not None:
        view = (existing.get("views") or {}).get("by_minute") or {}
        if view.get("map") == _HISTORY_MAP and view.get("reduce") == "_stats":
            return
        print("History view definition changed — CouchDB will rebuild the index.")

    document = {
        "_id": HISTORY_DESIGN_DOC,
        "language": "javascript",
        "views": {"by_minute": {"map": _HISTORY_MAP, "reduce": "_stats"}},
    }
    if existing is not None:
        document["_rev"] = existing["_rev"]

    _denoised_db.save(document)
    print("History view created — the first query builds the index.")


def _history_key(sensor_id: str, when, upper: bool = False) -> list:
    """
    A view key for one instant.

    An open bound becomes the sensor id alone at the low end, and the id plus
    an empty object at the high end — `{}` sorts after every number and array
    in CouchDB's collation, so it means "everything for this sensor".
    """

    if when is None:
        return [sensor_id, {}] if upper else [sensor_id]

    return [sensor_id, when.year, when.month, when.day, when.hour, when.minute]


def _view_rows(**options) -> list:
    try:
        return list(get_denoised_db().view(HISTORY_VIEW, **options))
    except ServerError as e:
        # (500, ('timeout', ...)) while the index builds.
        if "timeout" in str(e):
            raise HistoryIndexBuilding(
                "The history index is still building. This happens once, and "
                "takes a few minutes over a large database — retry shortly."
            ) from e
        raise
    except ResourceNotFound as e:
        # The design doc was deleted out from under us; recreate and let the
        # caller retry rather than failing every request from here on.
        _ensure_history_view()
        raise HistoryIndexBuilding(
            "The history view was missing and has been recreated — retry shortly."
        ) from e


def get_history_buckets(sensor_id: str, start, end, granularity: str) -> list[tuple]:
    """
    Bucketed min/max/avg for one sensor over a time range.

    Returns (key, stats) pairs where key is [sensor_id, year, month, ...]
    truncated to the granularity, and stats is CouchDB's _stats output.
    """

    return [
        (row.key, row.value)
        for row in _view_rows(
            group_level=GROUP_LEVELS[granularity],
            startkey=_history_key(sensor_id, start),
            endkey=_history_key(sensor_id, end, upper=True),
            # Half-open, so back-to-back ranges neither overlap nor gap.
            inclusive_end=False,
        )
    ]


def get_history_extent(sensor_id: str) -> dict | None:
    """
    The oldest and newest reading held for a sensor, and how many there are.

    This is what lets the UI bound its date picker to the data that exists
    rather than offering a calendar of empty days.
    """

    totals = _view_rows(group_level=1, startkey=[sensor_id], endkey=[sensor_id, {}])
    if not totals or not totals[0].value.get("count"):
        return None

    stats = totals[0].value

    bounds = {}
    for name, descending in (("oldest", False), ("newest", True)):
        rows = _view_rows(
            group_level=6,
            startkey=_history_key(sensor_id, None, upper=descending),
            endkey=_history_key(sensor_id, None, upper=not descending),
            descending=descending,
            limit=1,
        )
        bounds[name] = rows[0].key[1:] if rows else None

    return {
        "count": stats["count"],
        "min": stats["min"],
        "max": stats["max"],
        "avg": stats["sum"] / stats["count"],
        "oldest": bounds["oldest"],
        "newest": bounds["newest"],
    }


def get_document_counts():
    """
    What CouchDB currently holds, for the /api/storage status endpoint.

    Reports the outage instead of raising it, so /api/storage stays the place
    you look to find out *whether* CouchDB is up — mirroring mongo.stats().
    """

    try:
        _ensure_connected()
    except CouchDBUnavailable as e:
        return {"connected": False, "error": str(e)}

    try:
        # doc_count comes straight off the database metadata, so this is cheap
        # enough for a status endpoint to poll.
        return {
            "connected": True,
            "raw": _raw_db.info()["doc_count"],
            "denoised": _denoised_db.info()["doc_count"],
            "nodes": _nodes_db.info()["doc_count"],
        }
    except OSError as e:
        _reset()
        return {"connected": False, "error": str(e)}


def _reset() -> None:
    """
    Drop the cached handles after a connection dies mid-flight, so the next
    call reconnects rather than reusing a dead socket.
    """

    global _raw_db, _denoised_db, _nodes_db

    with _lock:
        _raw_db = _denoised_db = _nodes_db = None


def save_raw_document(document: dict):
    return get_raw_db().save(document)


def save_denoised_document(document: dict):
    return get_denoised_db().save(document)


def save_node_state(node_id: str, state: dict):
    # One document per node, rewritten on every packet: the map only ever needs
    # the newest position/health, and the history already lives in the raw db.
    nodes_db = get_nodes_db()
    doc_id = f"{NODE_DOC_PREFIX}{node_id}"

    for _ in range(2):
        document = dict(state)
        document["_id"] = doc_id

        existing = nodes_db.get(doc_id)
        if existing is not None:
            document["_rev"] = existing["_rev"]

        try:
            return nodes_db.save(document)
        except ResourceConflict:
            # Two packets from the same node raced; re-read the _rev and retry.
            continue

    raise ResourceConflict(f"Could not update node state for {node_id}")


def get_all_node_states():
    rows = get_nodes_db().view(
        "_all_docs",
        include_docs=True,
        startkey=NODE_DOC_PREFIX,
        # ￰ sorts after any realistic node id, so this is a prefix scan.
        endkey=NODE_DOC_PREFIX + "￰",
    )

    return [row.doc for row in rows if row.doc]


def get_latest_by_event_type(
    db,
    event_type: str,
    limit: int = 60
):
    # Mango sort requires the index above; returned newest-first, so we
    # reverse to give callers chronological (oldest -> newest) order.
    _, _, body = db.resource.post_json(
        "_find",
        body={
            "selector": {"event_type": event_type},
            "sort": [{"timestamp": "desc"}],
            "limit": limit,
        },
    )

    docs = body.get("docs", [])
    return list(reversed(docs))



def get_sensor_history(
    device_id: str,
    event_type: str,
    limit: int = 50
):
    # Served by device_event_timestamp_idx. The previous _all_docs scan read the
    # first 500 docs by _id — and _id is a random uuid, so it sampled an
    # arbitrary slice of the database rather than this sensor's recent history.
    _, _, body = get_raw_db().resource.post_json(
        "_find",
        body={
            "selector": {"device_id": device_id, "event_type": event_type},
            "sort": [{"timestamp": "desc"}],
            "limit": limit,
        },
    )

    # newest first
    return body.get("docs", [])

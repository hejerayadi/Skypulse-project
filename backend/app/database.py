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
from couchdb.http import ResourceConflict, ResourceNotFound, Unauthorized
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

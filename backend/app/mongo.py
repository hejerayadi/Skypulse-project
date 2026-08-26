"""
MongoDB Atlas mirror of the two telemetry streams.

CouchDB stays the store the dashboard reads from; Atlas is a second, cloud-side
copy of the same packets so the data outlives the local CouchDB container and
can be queried from anywhere. Two collections, matching the two CouchDB
databases: one for the packet exactly as Node-RED sent it, one for the same
packet after the denoisers have run.

Every write here is best effort. Atlas sits across the internet, and a packet
that already reached us must not be lost — or delayed — because the link to the
cloud is slow or down.
"""

from __future__ import annotations

import os
import queue
import threading

import dns.resolver
from dotenv import load_dotenv
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import PyMongoError
from pymongo.server_api import ServerApi

load_dotenv()

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "skypulse")

RAW_COLLECTION = os.getenv("MONGODB_RAW_COLLECTION", "raw_telemetry")
PREPROCESSED_COLLECTION = os.getenv(
    "MONGODB_PREPROCESSED_COLLECTION", "preprocessed_telemetry"
)

DNS_FALLBACK = os.getenv("MONGODB_DNS_FALLBACK", "8.8.8.8,1.1.1.1")

# Without a URI the mirror simply switches itself off, so the backend still runs
# against CouchDB alone (offline demo, or a teammate without Atlas access).
enabled = bool(MONGODB_URI)

# Built by start(), not at import: MongoClient resolves the mongodb+srv record
# inside its constructor, so building it here would fire a DNS lookup — and
# raise on a network that can't answer it — just from importing this module.
client: MongoClient | None = None
database = None


def _connect() -> None:
    global client, database

    client = MongoClient(
        MONGODB_URI,
        server_api=ServerApi("1"),
        # Every timeout is deliberately short. On the defaults, an unreachable
        # Atlas blocks each write for 30s of server selection, which at the
        # ~7 packets/second the nodes emit would pile up the writer queue in
        # seconds. Short timeouts degrade into dropped mirror writes instead.
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=10000,
    )
    database = client[MONGODB_DATABASE]


# Ingest hands documents to this queue and returns immediately; a single worker
# thread drains it in batches. That keeps Atlas round-trip latency (~100ms from
# here) off the request path that Node-RED is waiting on.
_QUEUE_MAXSIZE = 5000
_BATCH_MAXSIZE = 200

_queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
_stop = threading.Event()
_worker: threading.Thread | None = None


def _ensure_srv_resolvable() -> None:
    """
    Point dnspython at a public resolver when the local one can't answer SRV.

    A mongodb+srv:// URI carries no host list — the driver has to look up an SRV
    record to find the cluster. Plenty of home/ISP routers never answer that
    query type (the gateway on this network times out on it), which surfaces as
    a bare "resolution lifetime expired" and looks like Atlas being down. Falling
    back to a public resolver keeps the URI in .env exactly as Atlas hands it
    out, instead of pinning shard hostnames that Atlas is free to change.
    """

    if not MONGODB_URI.startswith("mongodb+srv://"):
        return

    # Strip credentials, path and query to get the cluster hostname.
    host = MONGODB_URI.split("://", 1)[1].split("@")[-1].split("/")[0].split("?")[0]
    srv_name = f"_mongodb._tcp.{host}"

    system_resolver = dns.resolver.Resolver()
    system_resolver.lifetime = 5
    try:
        system_resolver.resolve(srv_name, "SRV")
        return
    except Exception:
        pass

    nameservers = [server.strip() for server in DNS_FALLBACK.split(",") if server.strip()]
    if not nameservers:
        return

    public_resolver = dns.resolver.Resolver(configure=False)
    public_resolver.nameservers = nameservers
    public_resolver.lifetime = 10

    # pymongo resolves through the module-level default, so overriding it here
    # covers both the SRV and the TXT lookup the driver makes.
    dns.resolver.default_resolver = public_resolver

    print(f"Local DNS cannot resolve {srv_name}; using {', '.join(nameservers)} instead.")


def ping() -> bool:
    """Confirm the deployment answers, without raising into the caller."""

    if client is None:
        return False

    try:
        client.admin.command("ping")
        return True
    except PyMongoError as e:
        print("MongoDB Atlas ping failed:", e)
        return False


def ensure_indexes() -> None:
    """
    Mirror the CouchDB indexes, so the same queries the dashboard runs locally
    are cheap against Atlas too.
    """

    if database is None:
        return

    for name in (RAW_COLLECTION, PREPROCESSED_COLLECTION):
        collection = database[name]
        collection.create_index(
            [("device_id", ASCENDING), ("event_type", ASCENDING), ("timestamp", DESCENDING)],
            name="device_event_timestamp_idx",
        )
        collection.create_index(
            [("event_type", ASCENDING), ("timestamp", DESCENDING)],
            name="event_type_timestamp_idx",
        )


def start() -> None:
    """Bring the mirror up: connect, index, and start the writer thread."""

    global _worker

    if not enabled:
        print("MONGODB_URI is not set — Atlas mirror disabled.")
        return

    _ensure_srv_resolvable()

    try:
        _connect()
    except PyMongoError as e:
        # A bad URI or an unresolvable cluster leaves the mirror off rather than
        # stopping the backend from serving CouchDB.
        print("Could not connect to MongoDB Atlas:", e)
        return

    if ping():
        print(f"Connected to MongoDB Atlas ({MONGODB_DATABASE}).")

    try:
        ensure_indexes()
    except PyMongoError as e:
        # Indexes are an optimisation; missing ones must not stop the backend.
        print("Could not create Atlas indexes:", e)

    if _worker is None or not _worker.is_alive():
        _stop.clear()
        _worker = threading.Thread(target=_drain_forever, name="mongo-mirror", daemon=True)
        _worker.start()


def stop(timeout: float = 5.0) -> None:
    """Flush whatever is still queued, then shut the writer thread down."""

    if _worker is None:
        return

    _stop.set()
    _worker.join(timeout=timeout)

    if client is not None:
        client.close()


def stats() -> dict:
    """
    What the mirror currently holds, for the /api/storage status endpoint.

    Reported through the connection the backend already has, so checking it
    costs no extra DNS lookup or handshake.
    """

    if not enabled:
        return {"enabled": False, "connected": False}

    if database is None:
        return {"enabled": True, "connected": False, "database": MONGODB_DATABASE}

    result = {
        "enabled": True,
        "connected": True,
        "database": MONGODB_DATABASE,
        # Documents ingest has queued but the writer hasn't flushed yet. A number
        # that keeps climbing means Atlas is not keeping up with the nodes.
        "queue_depth": _queue.qsize(),
    }

    try:
        # estimated_document_count() reads collection metadata instead of
        # scanning, so this stays cheap to poll.
        result[RAW_COLLECTION] = database[RAW_COLLECTION].estimated_document_count()
        result[PREPROCESSED_COLLECTION] = database[
            PREPROCESSED_COLLECTION
        ].estimated_document_count()
    except PyMongoError as e:
        result["connected"] = False
        result["error"] = str(e)

    return result


def save_raw_document(document: dict) -> None:
    _enqueue(RAW_COLLECTION, document)


def save_preprocessed_document(document: dict) -> None:
    _enqueue(PREPROCESSED_COLLECTION, document)


def _enqueue(collection_name: str, document: dict) -> None:
    # database is only set once start() has connected; until then the mirror is
    # a no-op rather than a queue nobody drains.
    if database is None:
        return

    # Shallow copy: the caller hands the same dict to CouchDB straight after,
    # and couchdb-python stamps _id/_rev onto it in place. Those are CouchDB's
    # identifiers, not ours — Atlas assigns its own _id.
    try:
        _queue.put_nowait((collection_name, dict(document)))
    except queue.Full:
        print("Atlas mirror queue full — dropping document for", collection_name)


def _drain_forever() -> None:
    # Keep draining after stop() is set, so the shutdown flush isn't lossy.
    while not _stop.is_set() or not _queue.empty():
        try:
            batch = [_queue.get(timeout=0.25)]
        except queue.Empty:
            continue

        while len(batch) < _BATCH_MAXSIZE:
            try:
                batch.append(_queue.get_nowait())
            except queue.Empty:
                break

        _write_batch(batch)


def _write_batch(batch: list[tuple[str, dict]]) -> None:
    grouped: dict[str, list[dict]] = {}
    for collection_name, document in batch:
        grouped.setdefault(collection_name, []).append(document)

    for collection_name, documents in grouped.items():
        try:
            # ordered=False so one rejected document doesn't discard the rest of
            # the batch behind it.
            database[collection_name].insert_many(documents, ordered=False)
        except PyMongoError as e:
            print(f"Atlas mirror write failed ({collection_name}, {len(documents)} docs):", e)

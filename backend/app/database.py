from couchdb import Server
from couchdb.http import ResourceConflict, ResourceNotFound
from dotenv import load_dotenv
import os

load_dotenv()

server = Server(os.getenv("COUCHDB_URL"))

raw_db = server[os.getenv("COUCHDB_RAW_DATABASE")]
denoised_db = server[os.getenv("COUCHDB_DENOISED_DATABASE")]


def _get_or_create(name: str):
    try:
        return server[name]
    except ResourceNotFound:
        return server.create(name)


# The node registry is small (one document per node, overwritten in place) and
# has nothing to do with the telemetry history, so it gets its own database.
nodes_db = _get_or_create(os.getenv("COUCHDB_NODES_DATABASE", "skypulse-nodes"))

NODE_DOC_PREFIX = "node::"


def ensure_indexes():
    # The dashboard queries by event_type and sorts by timestamp; Mango
    # requires a matching index to run that query without a full scan.
    index = {
        "index": {"fields": ["event_type", "timestamp"]},
        "name": "event_type_timestamp_idx",
    }
    for db in (raw_db, denoised_db):
        db.resource.post_json("_index", body=index)

    # get_sensor_history() runs on every ingested packet, so it has to be an
    # indexed lookup: an unindexed scan of the raw db costs ~65ms a call, which
    # is more than the nodes leave between packets.
    history_index = {
        "index": {"fields": ["device_id", "event_type", "timestamp"]},
        "name": "device_event_timestamp_idx",
    }
    raw_db.resource.post_json("_index", body=history_index)


ensure_indexes()


def save_raw_document(document: dict):
    return raw_db.save(document)


def save_denoised_document(document: dict):
    return denoised_db.save(document)


def save_node_state(node_id: str, state: dict):
    # One document per node, rewritten on every packet: the map only ever needs
    # the newest position/health, and the history already lives in the raw db.
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
    rows = nodes_db.view(
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
    _, _, body = raw_db.resource.post_json(
        "_find",
        body={
            "selector": {"device_id": device_id, "event_type": event_type},
            "sort": [{"timestamp": "desc"}],
            "limit": limit,
        },
    )

    # newest first
    return body.get("docs", [])
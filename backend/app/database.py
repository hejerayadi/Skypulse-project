from couchdb import Server
from dotenv import load_dotenv
import os

load_dotenv()

server = Server(os.getenv("COUCHDB_URL"))

raw_db = server[os.getenv("COUCHDB_RAW_DATABASE")]
denoised_db = server[os.getenv("COUCHDB_DENOISED_DATABASE")]


def ensure_indexes():
    # The dashboard queries by event_type and sorts by timestamp; Mango
    # requires a matching index to run that query without a full scan.
    index = {
        "index": {"fields": ["event_type", "timestamp"]},
        "name": "event_type_timestamp_idx",
    }
    for db in (raw_db, denoised_db):
        db.resource.post_json("_index", body=index)


ensure_indexes()


def save_raw_document(document: dict):
    return raw_db.save(document)


def save_denoised_document(document: dict):
    return denoised_db.save(document)


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

    results = []

    for row in raw_db.view(
        "_all_docs",
        include_docs=True,
        limit=500
    ):

        doc = row.doc

        if (
            doc
            and doc.get("device_id") == device_id
            and doc.get("event_type") == event_type
        ):
            results.append(doc)


    # newest first
    results = sorted(
        results,
        key=lambda x: x["timestamp"],
        reverse=True
    )

    return results[:limit]
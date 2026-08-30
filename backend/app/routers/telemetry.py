from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app import mongo, mqtt
from app.database import HistoryIndexBuilding, get_document_counts
from app.services.ingest_service import TelemetryRejected, ingest_packet
from app.services.node_service import get_fleet
from app.services.telemetry_service import get_sensor_history, get_sensor_series


router = APIRouter()


@router.post("/telemetry")
async def receive_telemetry(request: Request):
    """
    HTTP ingest. Node-RED now publishes over MQTT instead (see app/mqtt.py), but
    this stays the way a sensor, a curl, or the frontend can push a packet
    directly — both paths run the same ingest_packet() pipeline.
    """

    data = await request.json()

    # Storing a packet is entirely blocking CouchDB I/O. Run it off the event
    # loop: inline, a single slow write stalls every other request, and at the
    # ~7 packets/second the nodes emit the server stops accepting connections
    # altogether.
    try:
        return await run_in_threadpool(ingest_packet, data, "http")
    except TelemetryRejected as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.get("/storage")
def get_storage():
    """
    Document counts in both stores, so you can see whether telemetry is still
    landing. Poll it twice and compare: a rising count means data is flowing.
    """

    return {
        "couchdb": get_document_counts(),
        "mongodb": mongo.stats(),
        # Where the packets are coming in from now — a connected broker with a
        # rising "received" is the MQTT equivalent of the counts above.
        "mqtt": mqtt.stats(),
    }


# Declared sync on purpose: FastAPI runs sync endpoints in a threadpool, so the
# blocking CouchDB reads below can't block the event loop.
@router.get("/nodes")
def get_nodes():
    return get_fleet()


@router.get("/telemetry/{sensor_id}")
def get_telemetry(
    sensor_id: str,
    samples: int = Query(60, ge=1, le=300),
):
    try:
        return get_sensor_series(sensor_id=sensor_id, samples=samples)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/telemetry/{sensor_id}/history")
def get_telemetry_history(
    sensor_id: str,
    start: datetime | None = Query(
        None,
        description="ISO-8601 start of the range, inclusive. Omit for everything stored.",
    ),
    end: datetime | None = Query(
        None,
        description="ISO-8601 end of the range, exclusive. Omit for 'up to now'.",
    ),
    granularity: str = Query(
        "auto",
        description="Bucket size: auto, minute, hour, day, month or year.",
        pattern="^(auto|minute|hour|day|month|year)$",
    ),
):
    """
    Bucketed history for one sensor, for browsing what has already been stored.

    Unlike /telemetry/{sensor_id}, which returns the newest N readings, this
    aggregates: a range can cover a month and a million readings, so each
    bucket carries min, max, average and how many readings it merges. The
    response also reports the sensor's full stored extent, so a date picker can
    be bounded to the data that actually exists.
    """

    # A naive datetime here means the caller left the timezone off. Treating it
    # as UTC matches how every timestamp is stored; guessing the server's local
    # zone would silently shift the range by hours.
    start = _as_utc(start)
    end = _as_utc(end)

    try:
        return get_sensor_history(
            sensor_id=sensor_id,
            start=start,
            end=end,
            granularity=granularity,
        )
    except HistoryIndexBuilding as e:
        # 503 rather than an empty chart: nothing is wrong, the answer just is
        # not ready yet, and the client should retry rather than conclude there
        # is no data.
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        # Unknown sensor is a 404; an inverted range is the caller's mistake.
        status = 404 if "Unknown sensor_id" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

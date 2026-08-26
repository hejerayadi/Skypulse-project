from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app import mongo, mqtt
from app.database import get_document_counts
from app.services.ingest_service import TelemetryRejected, ingest_packet
from app.services.node_service import get_fleet
from app.services.telemetry_service import get_sensor_series


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

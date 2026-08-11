from fastapi import APIRouter, HTTPException, Query, Request
from pydantic_core import ValidationError
from starlette.concurrency import run_in_threadpool

from app.models.telemetry import (
    EnvironmentTelemetry,
    MotionTelemetry,
    AcousticTelemetry,
    SystemStatusTelemetry,
)
from app.services.telemetry_service import get_sensor_series
from app.services.node_service import get_fleet, record_node_state
from app.database import (
    save_raw_document,
    save_denoised_document
)
from app.services.denoising_service import apply_denoising


router = APIRouter()

# Maps each event_type discriminator to the model that should validate it,
# since FastAPI can't infer the right Pydantic model from a plain dict body.
MODEL_REGISTRY = {
    "environment": EnvironmentTelemetry,
    "motion": MotionTelemetry,
    "acoustic": AcousticTelemetry,
    "status": SystemStatusTelemetry
}


@router.post("/telemetry")
async def receive_telemetry(request: Request):

    data = await request.json()

    # Storing a packet is entirely blocking CouchDB I/O. Run it off the event
    # loop: inline, a single slow write stalls every other request, and at the
    # ~7 packets/second the nodes emit the server stops accepting connections
    # altogether.
    return await run_in_threadpool(_store_telemetry, data)


def _store_telemetry(data: dict):

    print("Received:", data)

    # Identity, GPS, battery and health only exist on the outer envelope, so the
    # fleet map has to be fed before we drop down into the sensor reading.
    # A failure here must not cost us the telemetry itself.
    try:
        record_node_state(data)
    except Exception as e:
        print("Failed to record node state:", e)

    # Node-RED sends telemetry under "sensors"
    if "sensors" in data:
        data = data["sensors"]

    event_type = data.get("event_type")

    if not event_type:
        raise HTTPException(
            status_code=400,
            detail="Missing event_type"
        )

    model = MODEL_REGISTRY.get(event_type)

    if model is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown event type: {event_type}"
        )

    try:
        telemetry = model(**data)
    except ValidationError as e:
        raise HTTPException(
            status_code=422,
            detail=e.errors()
        )

    document = telemetry.model_dump(mode="json")

    denoised_document = apply_denoising(document.copy())

    save_raw_document(document)
    save_denoised_document(denoised_document)

    return {
        "message": "Telemetry stored successfully",
        "event_type": telemetry.event_type,
        "device_id": telemetry.device_id
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

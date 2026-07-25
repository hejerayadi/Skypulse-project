from fastapi import APIRouter, Query

from app.models.telemetry import Telemetry
from app.services.telemetry_service import generate_telemetry_series

router = APIRouter()


@router.post("/telemetry")
async def receive_telemetry(data: Telemetry):
    # This endpoint is here for the future ingestion path, when real devices post telemetry.

    return {
        "message": "Telemetry received successfully",
        "data": data
    }


@router.get("/telemetry/{sensor_id}")
async def get_telemetry(
    sensor_id: str,
    samples: int = Query(60, ge=1, le=300),
    interval_ms: int = Query(1000, ge=200, le=10000),
):
    # For now we synthesize a series so the frontend can treat the backend like a live telemetry source.
    return generate_telemetry_series(sensor_id=sensor_id, samples=samples, interval_ms=interval_ms)
from pydantic import BaseModel, Field
from datetime import datetime


class Telemetry(BaseModel):
    # This is the payload shape we can use later when real devices ingest telemetry.
    device_id: str = Field(..., example="station_01")

    timestamp: datetime

    temperature: float

    humidity: float

    pressure: float

    sound: float

    vibration: float


class TelemetryPoint(BaseModel):
    # Each point represents one sample in the chart series returned to the frontend.
    t: int
    v: float


class TelemetrySeries(BaseModel):
    # The dashboard only needs a small bundle of metadata plus the generated samples.
    sensor_id: str
    device_id: str
    unit: str
    latest: float
    delta: float
    points: list[TelemetryPoint]
    generated_at: datetime
    
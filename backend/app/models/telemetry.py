from datetime import datetime
from typing import Literal

from pydantic import BaseModel


# ==========================================================
# Base Classes
# ==========================================================

class TelemetryEvent(BaseModel):
    """
    Base class for every telemetry event received from a device.
    """

    device_id: str
    timestamp: datetime
    event_type: str


class Sensor(BaseModel):
    """
    Abstract sensor model.
    """

    value: float
    filtered_value: float | None = None
    unit: str


# ==========================================================
# Sensor Types
# ==========================================================

class TemperatureSensor(Sensor):
    pass


class HumiditySensor(Sensor):
    pass


class PressureSensor(Sensor):
    pass


class AcousticSensor(Sensor):
    pass


class AccelerometerSensor(BaseModel):
    accel_x: float
    accel_y: float
    accel_z: float
    magnitude: float


class GyroscopeSensor(BaseModel):
    gyro_x: float
    gyro_y: float
    gyro_z: float


# ==========================================================
# Shared Models
# ==========================================================

class Location(BaseModel):
    latitude: float
    longitude: float


class Abnormality(BaseModel):
    field: str
    value: float
    reason: str


# ==========================================================
# Environment Telemetry
# ==========================================================

class EnvironmentTelemetry(TelemetryEvent):
    event_type: Literal["environment"] = "environment"

    location: Location

    temperature: TemperatureSensor
    humidity: HumiditySensor
    pressure: PressureSensor

    abnormalities: list[Abnormality] = []


# ==========================================================
# Motion Telemetry
# ==========================================================

class MotionTelemetry(TelemetryEvent):
    event_type: Literal["motion"] = "motion"

    accelerometer: AccelerometerSensor
    gyroscope: GyroscopeSensor


# ==========================================================
# Acoustic Telemetry
# ==========================================================

class AcousticTelemetry(TelemetryEvent):
    event_type: Literal["acoustic"] = "acoustic"

    sound: AcousticSensor


# ==========================================================
# System Status Telemetry
# ==========================================================

class SystemStatusTelemetry(TelemetryEvent):
    event_type: Literal["status"] = "status"

    status: str
    battery: int | None = None
    uptime_seconds: int
    rssi: int


# ==========================================================
# Node Models
# ==========================================================

class NodeBattery(BaseModel):
    percentage: float | None = None
    charging: bool = False


class NodeHealth(BaseModel):
    status: str = "unknown"
    online: bool = True


class NodeEnvelope(BaseModel):
    """
    Outer payload the gateway wraps around every sensor reading. The sensor
    models below only describe the reading itself, so identity, GPS fix,
    battery and health live here.
    """

    node_id: str
    timestamp: datetime
    location: Location | None = None
    battery: NodeBattery | None = None
    health: NodeHealth | None = None


class NodeStatus(BaseModel):
    """
    A node as the fleet map sees it: last known position plus a single
    state the UI can colour a marker with.
    """

    node_id: str
    state: Literal["online", "degraded", "offline"]
    reason: str
    reported_status: str
    last_seen: datetime
    seconds_since_seen: float
    location: Location | None = None
    battery: NodeBattery | None = None
    last_event_type: str | None = None


class NodeFleet(BaseModel):
    nodes: list[NodeStatus]
    online: int
    degraded: int
    offline: int
    generated_at: datetime


# ==========================================================
# Dashboard Models (for frontend charts)
# ==========================================================

class TelemetryPoint(BaseModel):
    t: int
    v: float


class TelemetrySeries(BaseModel):
    sensor_id: str
    device_id: str
    unit: str
    latest: float
    delta: float
    points: list[TelemetryPoint]
    generated_at: datetime
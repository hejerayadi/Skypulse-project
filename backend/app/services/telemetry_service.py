from __future__ import annotations

from datetime import datetime, timezone

from app.database import denoised_db, get_latest_by_event_type
from app.models.telemetry import TelemetryPoint, TelemetrySeries


def _environment_field(field_name: str, fallback_unit: str):
	# temperature/humidity/pressure all live nested under an "environment" doc
	# and share the same {value, filtered_value, unit} shape.
	def extractor(doc: dict):
		sensor = doc.get(field_name) or {}
		value = sensor.get("filtered_value", sensor.get("value"))
		return value, sensor.get("unit", fallback_unit)

	return extractor


def _acoustic_extractor(doc: dict):
	sensor = doc.get("sound") or {}
	value = sensor.get("filtered_value", sensor.get("value"))
	return value, sensor.get("unit", "dB")


def _motion_extractor(doc: dict):
	# The denoiser only smooths accel_x/y/z in place; magnitude is the raw
	# reading, which is still the best single scalar for this chart.
	accel = doc.get("accelerometer") or {}
	return accel.get("magnitude"), "m/s²"


SENSOR_EVENT_MAP = {
	"temperature": {"event_type": "environment", "extract": _environment_field("temperature", "°C")},
	"humidity": {"event_type": "environment", "extract": _environment_field("humidity", "%")},
	"pressure": {"event_type": "environment", "extract": _environment_field("pressure", "hPa")},
	"acoustic": {"event_type": "acoustic", "extract": _acoustic_extractor},
	"motion": {"event_type": "motion", "extract": _motion_extractor},
}


def get_sensor_series(sensor_id: str, samples: int = 60) -> TelemetrySeries:
	mapping = SENSOR_EVENT_MAP.get(sensor_id)
	if mapping is None:
		raise ValueError(f"Unknown sensor_id: {sensor_id!r}")

	# Devices aren't distinguished in the dashboard, so this merges every
	# device's readings for the sensor into one chronological series.
	docs = get_latest_by_event_type(denoised_db, mapping["event_type"], limit=samples)

	points: list[TelemetryPoint] = []
	unit = ""
	device_id = "unknown"

	for doc in docs:
		value, doc_unit = mapping["extract"](doc)
		if value is None:
			continue

		timestamp = datetime.fromisoformat(doc["timestamp"])
		points.append(TelemetryPoint(t=int(timestamp.timestamp() * 1000), v=float(value)))
		unit = doc_unit or unit
		device_id = doc.get("device_id", device_id)

	latest = points[-1].v if points else 0.0
	previous = points[-2].v if len(points) > 1 else latest

	return TelemetrySeries(
		sensor_id=sensor_id,
		device_id=device_id,
		unit=unit,
		latest=latest,
		delta=latest - previous,
		points=points,
		generated_at=datetime.now(tz=timezone.utc),
	)

from __future__ import annotations

import logging
from datetime import datetime, timezone
from math import sin
from random import Random

from app.denoising import denoise_acoustic_points, denoise_motion_points, denoise_pressure_points, denoise_temperature_points
from app.models.telemetry import TelemetryPoint, TelemetrySeries


logger = logging.getLogger(__name__)


# These profiles keep the synthetic telemetry stable enough to read while still looking live.
SENSOR_PROFILES: dict[str, dict[str, float | str]] = {
	"temperature": {"unit": "°C", "base": 22.0, "noise": 0.8, "min": -30.0, "max": 60.0},
	"humidity": {"unit": "%", "base": 45.0, "noise": 1.5, "min": 0.0, "max": 100.0},
	"pressure": {"unit": "hPa", "base": 1013.0, "noise": 2.8, "min": 900.0, "max": 1080.0},
	"acoustic": {"unit": "Hz", "base": 1000.0, "noise": 22.0, "min": 80.0, "max": 5000.0},
	"motion": {"unit": "m/s²", "base": 0.3, "noise": 0.4, "min": 0.0, "max": 20.0},
}


def _resolve_profile(sensor_id: str) -> dict[str, float | str]:
	# If the frontend asks for an unknown sensor, fall back to a neutral profile instead of failing.
	return SENSOR_PROFILES.get(
		sensor_id,
		{"unit": "u", "base": 0.0, "noise": 0.5, "min": -100.0, "max": 100.0},
	)


def _generate_value(sensor_id: str, timestamp_ms: int, sample_index: int) -> float:
	# Use a deterministic seed per point so the line looks smooth while still changing on refresh.
	profile = _resolve_profile(sensor_id)
	rng = Random(f"{sensor_id}:{timestamp_ms}:{sample_index}")
	base = float(profile["base"])
	noise = float(profile["noise"])
	minimum = float(profile["min"])
	maximum = float(profile["max"])

	if sensor_id == "pressure":
		# Pressure is intentionally a bit more erratic so the Kalman filter has visible work to do.
		slow_wave = sin(timestamp_ms / 9000 + base) * (noise * 4)
		fast_noise = (rng.random() - 0.5) * noise * 6
		spike = 0.0
		if rng.random() < 0.2:
			spike_direction = -1.0 if rng.random() < 0.5 else 1.0
			spike = spike_direction * (2.5 + rng.random() * 4.5)
		raw_value = base + slow_wave + fast_noise + spike
		return max(minimum, min(maximum, raw_value))

	if sensor_id == "temperature":
		# Temperature gets isolated spikes so the median filter can visibly remove them.
		slow_wave = sin(timestamp_ms / 15000 + base) * (noise * 2.5)
		fast_noise = (rng.random() - 0.5) * noise * 4
		spike = 0.0
		if rng.random() < 0.35:
			spike_direction = -1.0 if rng.random() < 0.5 else 1.0
			spike = spike_direction * (3.0 + rng.random() * 6.0)
		raw_value = base + slow_wave + fast_noise + spike
		return max(minimum, min(maximum, raw_value))

	if sensor_id == "motion":
		# Motion keeps a slow physical trend with fast jitter, which is what the low-pass filter should remove.
		slow_wave = sin(timestamp_ms / 25000 + base) * (noise * 5)
		fast_noise = (rng.random() - 0.5) * noise * 10
		spike = 0.0
		if rng.random() < 0.25:
			spike_direction = -1.0 if rng.random() < 0.5 else 1.0
			spike = spike_direction * (0.8 + rng.random() * 2.2)
		raw_value = base + slow_wave + fast_noise + spike
		return max(minimum, min(maximum, raw_value))

	if sensor_id == "acoustic":
		# Acoustic is a scalar Hz estimate, so we smooth it with a low-pass filter rather than audio band-pass filtering.
		slow_wave = sin(timestamp_ms / 18000 + base) * (noise * 2.2)
		fast_noise = (rng.random() - 0.5) * noise * 8
		spike = 0.0
		if rng.random() < 0.28:
			spike_direction = -1.0 if rng.random() < 0.5 else 1.0
			spike = spike_direction * (40.0 + rng.random() * 120.0)
		raw_value = base + slow_wave + fast_noise + spike
		return max(minimum, min(maximum, raw_value))

	slow_wave = sin(timestamp_ms / 12000 + base) * (noise * 3)
	fast_noise = (rng.random() - 0.5) * noise * 2
	raw_value = base + slow_wave + fast_noise
	return max(minimum, min(maximum, raw_value))


def generate_telemetry_series(sensor_id: str, samples: int, interval_ms: int) -> TelemetrySeries:
	# The frontend receives the full history each time, which keeps the hook simple for now.
	profile = _resolve_profile(sensor_id)
	now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
	points: list[TelemetryPoint] = []
	raw_values: list[float] = []

	for index in range(samples):
		point_time = now_ms - (samples - index - 1) * interval_ms
		raw_value = _generate_value(sensor_id, point_time, index)
		raw_values.append(raw_value)
		points.append(TelemetryPoint(t=point_time, v=raw_value))

	if sensor_id == "pressure":
		filtered_values = denoise_pressure_points(raw_values)
		points = [TelemetryPoint(t=point.t, v=filtered_value) for point, filtered_value in zip(points, filtered_values)]
		raw_latest = raw_values[-1] if raw_values else float(profile["base"])
		filtered_latest = filtered_values[-1] if filtered_values else raw_latest
		print(
			f"PRESSURE DENOISING: raw_latest={raw_latest:.4f} "
			f"filtered_latest={filtered_latest:.4f} "
			f"delta={(filtered_latest - raw_latest):.4f} samples={samples}",
			flush=True,
		)

	if sensor_id == "temperature":
		filtered_values = denoise_temperature_points(raw_values, kernel_size=5)
		points = [TelemetryPoint(t=point.t, v=filtered_value) for point, filtered_value in zip(points, filtered_values)]
		raw_latest = raw_values[-1] if raw_values else float(profile["base"])
		filtered_latest = filtered_values[-1] if filtered_values else raw_latest
		print(
			f"TEMPERATURE DENOISING ACTIVE: raw_latest={raw_latest:.4f} "
			f"filtered_latest={filtered_latest:.4f} "
			f"delta={(filtered_latest - raw_latest):.4f} samples={samples}",
			flush=True,
		)

	if sensor_id == "motion":
		filtered_values = denoise_motion_points(raw_values, sample_rate_hz=1.0, cutoff_hz=0.15)
		points = [TelemetryPoint(t=point.t, v=filtered_value) for point, filtered_value in zip(points, filtered_values)]
		raw_latest = raw_values[-1] if raw_values else float(profile["base"])
		filtered_latest = filtered_values[-1] if filtered_values else raw_latest
		print(
			f"MOTION LOW-PASS: raw_latest={raw_latest:.4f} "
			f"filtered_latest={filtered_latest:.4f} "
			f"delta={(filtered_latest - raw_latest):.4f} samples={samples}",
			flush=True,
		)

	if sensor_id == "acoustic":
		filtered_values = denoise_acoustic_points(raw_values, sample_rate_hz=1.0, cutoff_hz=0.18)
		points = [TelemetryPoint(t=point.t, v=filtered_value) for point, filtered_value in zip(points, filtered_values)]
		raw_latest = raw_values[-1] if raw_values else float(profile["base"])
		filtered_latest = points[-1].v if points else raw_latest
		print(
			f"ACOUSTIC LOW-PASS: raw_latest={raw_latest:.4f} "
			f"filtered_latest={filtered_latest:.4f} "
			f"delta={(filtered_latest - raw_latest):.4f} samples={samples}",
			flush=True,
		)

	latest = points[-1].v if points else float(profile["base"])
	previous = points[-2].v if len(points) > 1 else latest

	return TelemetrySeries(
		sensor_id=sensor_id,
		device_id="station_01",
		unit=str(profile["unit"]),
		latest=latest,
		delta=latest - previous,
		points=points,
		generated_at=datetime.now(tz=timezone.utc),
	)

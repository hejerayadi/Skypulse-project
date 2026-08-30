from __future__ import annotations

import math

from datetime import datetime, timedelta, timezone

from app.database import (
	RAW_SUFFIX,
	get_denoised_db,
	get_history_buckets,
	get_history_extent,
	get_latest_by_event_type,
)
from app.models.telemetry import TelemetryPoint, TelemetrySeries


# Each extractor returns (denoised, raw, unit). The raw reading is carried
# alongside so the chart can show what the filter removed; a stored document
# keeps both, so this costs nothing extra to read.

def _environment_field(field_name: str, fallback_unit: str):
	# temperature/humidity/pressure all live nested under an "environment" doc
	# and share the same {value, filtered_value, unit} shape.
	def extractor(doc: dict):
		sensor = doc.get(field_name) or {}
		raw = sensor.get("value")
		value = sensor.get("filtered_value", raw)
		return value, raw, sensor.get("unit", fallback_unit)

	return extractor


def _acoustic_extractor(doc: dict):
	sensor = doc.get("sound") or {}
	raw = sensor.get("value")
	value = sensor.get("filtered_value", raw)
	return value, raw, sensor.get("unit", "dB")


def _motion_extractor(doc: dict):
	# Motion is the exception: the denoiser rewrites accel_x/y/z in place and
	# leaves `magnitude` alone, so `magnitude` *is* the raw reading and the
	# filtered one has to be recomputed from the filtered components.
	accel = doc.get("accelerometer") or {}
	raw = accel.get("magnitude")

	components = [accel.get("accel_x"), accel.get("accel_y"), accel.get("accel_z")]
	if all(isinstance(c, (int, float)) for c in components):
		value = math.sqrt(sum(c * c for c in components))
	else:
		value = raw

	return value, raw, "m/s²"


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
	# Resolved per call rather than imported: the connection is opened on first
	# use, so holding a module-level handle would reintroduce the import-time
	# connect this was moved away from.
	docs = get_latest_by_event_type(get_denoised_db(), mapping["event_type"], limit=samples)

	points: list[TelemetryPoint] = []
	unit = ""
	device_id = "unknown"

	for doc in docs:
		value, raw, doc_unit = mapping["extract"](doc)
		if value is None:
			continue

		timestamp = datetime.fromisoformat(doc["timestamp"])
		points.append(
			TelemetryPoint(
				t=int(timestamp.timestamp() * 1000),
				v=float(value),
				raw=None if raw is None else float(raw),
			)
		)
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


# ---------------------------------------------------------------------------
# Historical browsing
#
# get_sensor_series() above answers "what is happening now" by reading the
# newest N documents. This answers "what happened between these two dates",
# which is a different question with a different cost: the range can span a
# month and a million readings, so the answer has to be aggregated rather than
# enumerated. app.database's history view does the aggregation; everything here
# is about choosing a sensible bucket size and shaping the result.
# ---------------------------------------------------------------------------

SENSOR_UNITS = {
	"temperature": "°C",
	"humidity": "%",
	"pressure": "hPa",
	"acoustic": "dB",
	"motion": "m/s²",
}

# Ordered coarsest-last, so _coarser() can step down the list.
GRANULARITIES = ["minute", "hour", "day", "month", "year"]

BUCKET_SECONDS = {
	"minute": 60,
	"hour": 3600,
	"day": 86400,
	"month": 86400 * 30,
	"year": 86400 * 365,
}

# Past this a line chart is drawing several points per pixel, and the payload
# costs more than the detail is worth. Auto granularity never gets close; an
# explicit one is coarsened until it fits, and the response says so.
MAX_BUCKETS = 1500


def _auto_granularity(span: timedelta) -> str:
	"""
	Bucket size that keeps a range readable without asking the caller to think
	about it.

	Each threshold is the span at which the next size down would exceed
	MAX_BUCKETS: a day of minutes is 1440, six weeks of hours is 1080, three
	years of days is 1095. Erring towards the finer size on purpose — "last 24
	hours" is the most common range anyone asks for, and hourly averages hide
	exactly the short spikes a fault dashboard exists to show.
	"""

	hours = span.total_seconds() / 3600

	if hours <= 25:
		return "minute"
	if hours <= 24 * 45:
		return "hour"
	if hours <= 24 * 365 * 3:
		return "day"
	return "month"


def _coarser(granularity: str) -> str | None:
	index = GRANULARITIES.index(granularity)
	return GRANULARITIES[index + 1] if index + 1 < len(GRANULARITIES) else None


def _key_to_timestamp(key: list) -> datetime:
	"""
	Turn a view key back into an instant.

	The key is [sensor_id, year, month, day, hour, minute] truncated to the
	granularity, so the missing tail is filled with the start of the period —
	a day bucket is that day at 00:00 UTC.
	"""

	parts = list(key[1:])
	# month and day are 1-based; hour and minute are 0-based.
	defaults = [1970, 1, 1, 0, 0]
	while len(parts) < 5:
		parts.append(defaults[len(parts)])

	year, month, day, hour, minute = parts
	return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _extent_timestamp(parts: list | None) -> datetime | None:
	if not parts:
		return None
	return _key_to_timestamp([None, *parts])


def get_sensor_history(
	sensor_id: str,
	start: datetime | None = None,
	end: datetime | None = None,
	granularity: str = "auto",
) -> dict:
	"""
	Bucketed history for one sensor between two instants.

	`start`/`end` are half-open and either may be None for an open bound.
	`granularity` of "auto" picks a bucket size from the span; an explicit one
	is honoured unless it would produce an unreasonable number of buckets, in
	which case it is coarsened and `granularity_adjusted` says so.
	"""

	if sensor_id not in SENSOR_UNITS:
		raise ValueError(f"Unknown sensor_id: {sensor_id!r}")

	if start and end and start >= end:
		raise ValueError("`start` must be before `end`.")

	# What exists at all, so the caller can bound a date picker to real data
	# and tell "no readings in this range" apart from "this sensor has none".
	extent = get_history_extent(sensor_id)

	oldest = _extent_timestamp(extent["oldest"]) if extent else None
	newest = _extent_timestamp(extent["newest"]) if extent else None

	requested = granularity
	if granularity == "auto":
		# Fall back to the data's own span when a bound is open, so "everything"
		# does not get minute buckets for a month.
		span_start = start or oldest or datetime.now(tz=timezone.utc)
		span_end = end or newest or datetime.now(tz=timezone.utc)
		granularity = _auto_granularity(max(span_end - span_start, timedelta(0)))
	elif granularity not in GRANULARITIES:
		raise ValueError(
			f"Unknown granularity: {granularity!r}. "
			f"Expected 'auto' or one of {', '.join(GRANULARITIES)}."
		)

	# Coarsen before querying rather than after: the point is to avoid building
	# a 40,000-row response, not to build one and then throw it away.
	if start and end:
		while _estimated_buckets(start, end, granularity) > MAX_BUCKETS:
			coarser = _coarser(granularity)
			if coarser is None:
				break
			granularity = coarser

	rows = get_history_buckets(sensor_id, start, end, granularity)

	# The same buckets before the filters ran. Keyed by timestamp rather than
	# zipped: a bucket can exist in one series and not the other (motion only
	# emits a filtered value when all three axes are present), and zipping two
	# lists of different lengths would silently shift the whole curve.
	raw_rows = get_history_buckets(sensor_id + RAW_SUFFIX, start, end, granularity)
	raw_by_time = {
		int(_key_to_timestamp(key).timestamp() * 1000): stats
		for key, stats in raw_rows
		if stats.get("count")
	}

	buckets = []
	for key, stats in rows:
		if not stats.get("count"):
			continue

		at = int(_key_to_timestamp(key).timestamp() * 1000)
		bucket = {
			"t": at,
			"min": round(stats["min"], 4),
			"max": round(stats["max"], 4),
			"avg": round(stats["sum"] / stats["count"], 4),
			"count": stats["count"],
		}

		raw = raw_by_time.get(at)
		if raw:
			bucket["raw_min"] = round(raw["min"], 4)
			bucket["raw_max"] = round(raw["max"], 4)
			bucket["raw_avg"] = round(raw["sum"] / raw["count"], 4)

		buckets.append(bucket)

	return {
		"sensor_id": sensor_id,
		"unit": SENSOR_UNITS[sensor_id],
		"granularity": granularity,
		"granularity_adjusted": requested not in ("auto", granularity),
		"start": start.isoformat() if start else None,
		"end": end.isoformat() if end else None,
		"buckets": buckets,
		"summary": _summarise(rows),
		"raw": {
			"available": bool(raw_by_time),
			"summary": _summarise(raw_rows),
			# True when the filter changed nothing anywhere in this range —
			# either the sensor has no denoiser, or it is a no-op. The UI says
			# so rather than drawing two curves exactly on top of each other.
			"identical": _identical(buckets),
		},
		# The whole history held for this sensor, regardless of the range asked
		# for — this is the "available history" the date picker is bounded to.
		"extent": {
			"oldest": oldest.isoformat() if oldest else None,
			"newest": newest.isoformat() if newest else None,
			"count": extent["count"] if extent else 0,
			"min": round(extent["min"], 4) if extent else None,
			"max": round(extent["max"], 4) if extent else None,
			"avg": round(extent["avg"], 4) if extent else None,
		},
		"generated_at": datetime.now(tz=timezone.utc).isoformat(),
	}


def _identical(buckets: list[dict]) -> bool:
	"""
	Whether the denoised and raw curves are the same everywhere.

	Compared on the rounded values the client is actually given, so this agrees
	with what the chart would draw rather than with a difference too small to
	render.
	"""

	compared = [b for b in buckets if "raw_avg" in b]
	if not compared:
		return False

	return all(
		b["avg"] == b["raw_avg"] and b["min"] == b["raw_min"] and b["max"] == b["raw_max"]
		for b in compared
	)


def _estimated_buckets(start: datetime, end: datetime, granularity: str) -> float:
	return (end - start).total_seconds() / BUCKET_SECONDS[granularity]


def _summarise(rows: list[tuple]) -> dict:
	"""
	Min/max/average over the whole range.

	Recombined from the buckets rather than queried separately, and the average
	is weighted by each bucket's count — averaging the bucket averages would
	give a minute with two readings the same weight as one with two hundred.
	"""

	total = sum(stats["count"] for _, stats in rows)
	if total == 0:
		return {"min": None, "max": None, "avg": None, "count": 0, "buckets": 0}

	return {
		"min": round(min(stats["min"] for _, stats in rows), 4),
		"max": round(max(stats["max"] for _, stats in rows), 4),
		"avg": round(sum(stats["sum"] for _, stats in rows) / total, 4),
		"count": total,
		"buckets": len(rows),
	}

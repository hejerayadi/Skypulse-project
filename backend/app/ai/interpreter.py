"""
The three interpretations the dashboard asks for, and the cache that makes them
affordable.

Nothing here is on a timer. A note is written when the reader asks for one —
opening a page, changing a chart's range, or pressing refresh — and never
because a clock ticked. The RF stream produces a prediction every 2 seconds and
the live chart redraws every second; writing a paragraph about each would be
thousands of completions an hour, all saying the same thing.

Between those requests the cache decides whether the question is actually new.
Every interpretation carries a *signature* describing the situation it explains
— the fault label and severity, the shape of a live trace, the exact history
query — and the model is only asked again when that signature changes. Two
rules bracket it:

  * `min_interval` stops a jittery signature from firing on every request;
  * `max_age` retires a note that has been cached long enough to have gone
    stale.

`force` overrides both, and is what the refresh button sends: a reader who
presses it has said the cached answer is not good enough, so re-reading the
cache back to them would make the button look broken.

The cache is server-side and shared, which is the other half of the saving: ten
operators watching the same fault cost one completion, not ten.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from app.ai import client
from app.ai.client import AIUnavailable
from app.rf_fault import stream as rf_stream
from app.services.telemetry_service import (
    SENSOR_UNITS,
    get_sensor_history,
    get_sensor_series,
)

# Per-kind cache policy, in seconds.
#
# RF refreshes fastest because it is the one that has to keep up with an alarm;
# history barely refreshes at all because a past range does not change.
POLICY = {
    "rf": {"min_interval": 10.0, "max_age": 180.0},
    "live": {"min_interval": 30.0, "max_age": 240.0},
    "history": {"min_interval": 0.0, "max_age": 600.0},
}

# A floor under `force`, so a double-clicked button is one completion and not
# two. Sized for an accidental double-click and nothing more: anything longer
# swallows a deliberate press a second or two after the page loaded, and a
# refresh button that returns the sentence already on screen looks broken.
FORCE_FLOOR_SECONDS = 1.0

SYSTEM_PROMPT = (
    "You are the telemetry analyst on SkyPulse, a CubeSat ground station. "
    "An operator is looking at a chart and needs to know what it means. "
    "Explain what is happening and why, using the numbers you are given. "
    "Be specific and calm. Never invent a value that is not in the data. "
    "Plain prose only: no markdown, no bullet points, no headings, no preamble, "
    "and never begin with 'The chart' or 'This shows'."
)


class _Entry:
    __slots__ = ("text", "signature", "at")

    def __init__(self, text: str, signature: str, at: float):
        self.text = text
        self.signature = signature
        self.at = at


_cache: dict[str, _Entry] = {}
_cache_lock = threading.Lock()
# One lock per cache key, so a burst of browsers asking the same question at the
# same moment produces one completion rather than one each.
_key_locks: dict[str, threading.Lock] = {}

_calls = 0
_hits = 0


def stats() -> dict:
    return {**client.status(), "completions": _calls, "cache_hits": _hits, "cached_keys": len(_cache)}


def _key_lock(key: str) -> threading.Lock:
    with _cache_lock:
        return _key_locks.setdefault(key, threading.Lock())


def _fresh(entry: _Entry | None, signature: str, policy: dict) -> bool:
    if entry is None:
        return False

    age = time.monotonic() - entry.at

    if age >= policy["max_age"]:
        return False
    if entry.signature != signature and age >= policy["min_interval"]:
        return False

    return True


def _usable(entry: _Entry | None, signature: str, policy: dict, force: bool) -> bool:
    if entry is None:
        return False

    if force:
        # Only the double-click floor survives an explicit refresh.
        return (time.monotonic() - entry.at) < FORCE_FLOOR_SECONDS

    return _fresh(entry, signature, policy)


def _interpret(kind: str, key: str, signature: str, prompt: str, force: bool = False) -> dict:
    """Return a cached note, or ask the model for a new one."""

    global _calls, _hits

    policy = POLICY[kind]

    with _cache_lock:
        entry = _cache.get(key)

    if _usable(entry, signature, policy, force):
        _hits += 1
        return _envelope(entry, cached=True)

    with _key_lock(key):
        # Re-check inside the lock: whoever held it may have just refreshed.
        with _cache_lock:
            entry = _cache.get(key)
        if _usable(entry, signature, policy, force):
            _hits += 1
            return _envelope(entry, cached=True)

        try:
            text = client.complete(SYSTEM_PROMPT, prompt)
        except AIUnavailable:
            # A failed refresh should not blank a note that is already on
            # screen; serve the stale one and let the caller see it is stale.
            if entry is not None:
                return _envelope(entry, cached=True, stale=True)
            raise

        _calls += 1
        entry = _Entry(text, signature, time.monotonic())

        with _cache_lock:
            _cache[key] = entry

    return _envelope(entry, cached=False)


def _envelope(entry: _Entry, cached: bool, stale: bool = False) -> dict:
    return {
        "interpretation": entry.text,
        "signature": entry.signature,
        "cached": cached,
        # True when the model could not be reached and this is the last note we
        # managed to produce.
        "stale": stale,
        "age_seconds": round(time.monotonic() - entry.at, 1),
        "model": client.MODEL,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# 1. The ground station's RF fault state
# ---------------------------------------------------------------------------

def _eta_band(eta: float | None) -> str:
    if eta is None:
        return "none"
    if eta <= 30:
        return "imminent"
    if eta <= 120:
        return "soon"
    return "later"


def interpret_rf(force: bool = False) -> dict:
    """
    Explain the current RF link state.

    Signed by label, severity, a coarse confidence and an ETA band rather than
    by the raw numbers: the probability wobbles a few percent every packet, and
    re-explaining an unchanged alert because 0.97 became 0.96 is exactly the
    waste the cache exists to prevent.
    """

    prediction = rf_stream.latest()

    if prediction is None or not prediction.get("window_filled"):
        raise ValueError("No RF prediction yet — the model window is still filling.")

    label = prediction["label"]
    severity = prediction["severity"]
    confidence = prediction.get("confidence") or 0.0
    eta = prediction.get("eta_seconds")

    signature = f"{label}|{severity}|{round(confidence * 10)}|{_eta_band(eta)}"

    reading = prediction.get("reading", {})
    probabilities = prediction.get("class_probabilities", {})
    drivers = prediction.get("drivers", [])[:3]

    lines = [
        "Ground station RF link, classified by a random-forest model over a "
        "20-step window of receiver telemetry.",
        "",
        f"Verdict: {label} ({severity}), confidence {confidence:.0%}.",
        "Class probabilities: "
        + ", ".join(f"{name} {value:.0%}" for name, value in sorted(probabilities.items())),
    ]

    if eta is not None and prediction.get("eta_basis"):
        when = "already past the threshold" if eta <= 1 else f"in about {eta:.0f} seconds"
        lines.append(f"Forecast: {prediction['eta_basis']} {when}.")

    lines.append("")
    lines.append("Current readings:")
    lines.append(f"- SDR temperature {reading.get('sdr_temperature_c')} C")
    lines.append(f"- Frequency offset {reading.get('frequency_offset_hz')} Hz")
    lines.append(f"- Wind speed {reading.get('wind_speed_mps')} m/s")
    lines.append(f"- Antenna pointing error {reading.get('antenna_pointing_error_deg')} degrees")
    lines.append(f"- SNR {reading.get('snr')} dB, signal lock {reading.get('signal_lock')}%")

    if drivers:
        lines.append("")
        lines.append("Trends over the window (per second):")
        for driver in drivers:
            lines.append(
                f"- {driver['label']}: {driver['value']} {driver['unit']}, "
                f"trending {driver['trend']:+} {driver['unit']}/s"
            )

    lines.append("")
    lines.append(
        "In at most two short sentences: say what state the link is in and what "
        "is causing it, and if something is developing, what the operator should "
        "watch or do. Do not mention the model, the probabilities, or this prompt."
    )

    return _interpret("rf", "rf", signature, "\n".join(lines), force)


# ---------------------------------------------------------------------------
# 2. A sensor's live trace
# ---------------------------------------------------------------------------

def _shape(values: list[float]) -> tuple[str, float, float, float]:
    low = min(values)
    high = max(values)
    average = sum(values) / len(values)

    third = max(1, len(values) // 3)
    head = sum(values[:third]) / third
    tail = sum(values[-third:]) / third
    span = high - low or 1.0

    if abs(tail - head) < span * 0.15:
        trend = "steady"
    else:
        trend = "rising" if tail > head else "falling"

    return trend, low, high, average


def interpret_sensor_live(sensor_id: str, force: bool = False) -> dict:
    """
    Explain what a sensor's live trace is doing right now.

    Signed by the *shape* of the trace — trend plus rounded level and spread —
    not by the newest reading. A noisy sensor changes its last value every
    second while telling the same story, and the story is what the note says.
    """

    if sensor_id not in SENSOR_UNITS:
        raise ValueError(f"Unknown sensor_id: {sensor_id!r}")

    series = get_sensor_series(sensor_id=sensor_id, samples=90)
    points = series.points

    if not points:
        raise ValueError(f"No live readings for {sensor_id!r} yet.")

    values = [p.v for p in points]
    trend, low, high, average = _shape(values)
    unit = series.unit or SENSOR_UNITS[sensor_id]

    # Rounded hard, so ordinary jitter does not look like a new situation.
    signature = f"{sensor_id}|{trend}|{round(average, 1)}|{round(high - low, 1)}"

    raw_values = [p.raw for p in points if p.raw is not None]
    filter_note = ""
    if raw_values and len(raw_values) == len(values):
        removed = sum(abs(v - r) for v, r in zip(values, raw_values)) / len(values)
        filter_note = (
            f"Denoising is shifting readings by {removed:.3f} {unit} on average."
            if removed > 0
            else "Denoising is leaving these readings unchanged."
        )

    prompt = "\n".join(
        [
            f"Live {sensor_id} trace on a CubeSat ground station, "
            f"{len(values)} readings at roughly one per second (oldest to newest).",
            "",
            f"Latest: {values[-1]:.2f} {unit}",
            f"Range over the window: {low:.2f} to {high:.2f} {unit}, average {average:.2f} {unit}",
            f"Overall movement: {trend}",
            filter_note,
            "",
            "Recent values: " + ", ".join(f"{v:.2f}" for v in values[-24:]),
            "",
            "In at most two short sentences: describe what this signal is doing "
            "right now — its level, how steady it is, and anything worth "
            "watching. Do not restate the numbers one by one.",
        ]
    )

    return _interpret("live", f"live:{sensor_id}", signature, prompt, force)


# ---------------------------------------------------------------------------
# 3. A sensor's stored history, for whatever range is on screen
# ---------------------------------------------------------------------------

def _when(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).strftime("%d %b %H:%M UTC")


def interpret_sensor_history(
    sensor_id: str,
    start: datetime | None,
    end: datetime | None,
    granularity: str = "auto",
    force: bool = False,
) -> dict:
    """
    Explain the history curve currently on screen.

    Signed by the query itself, so every change of range or bucket size is a
    new question and gets a new answer — which is the point: the note is there
    to tell the operator what the curve they just asked for actually shows.
    """

    history = get_sensor_history(sensor_id=sensor_id, start=start, end=end, granularity=granularity)
    buckets = history["buckets"]

    if not buckets:
        raise ValueError("No readings in this range to interpret.")

    unit = history["unit"]
    summary = history["summary"]

    coldest = min(buckets, key=lambda b: b["min"])
    hottest = max(buckets, key=lambda b: b["max"])
    # The bucket with the widest internal spread — where the signal was most
    # unsettled, which is usually the interesting moment in a long range.
    noisiest = max(buckets, key=lambda b: b["max"] - b["min"])

    signature = "|".join(
        [
            sensor_id,
            history["start"] or "open",
            history["end"] or "open",
            history["granularity"],
            str(summary["buckets"]),
        ]
    )

    raw = history.get("raw", {})
    if raw.get("identical"):
        filter_note = "Denoising left this range completely unchanged."
    elif raw.get("available"):
        raw_avg = raw["summary"].get("avg")
        filter_note = (
            f"Before denoising the average was {raw_avg} {unit}; after it is {summary['avg']} {unit}."
        )
    else:
        filter_note = ""

    prompt = "\n".join(
        [
            f"Stored {sensor_id} history from a CubeSat ground station.",
            "",
            f"Range shown: {_when(buckets[0]['t'])} to {_when(buckets[-1]['t'])}",
            f"Bucket size: one per {history['granularity']} "
            f"({summary['buckets']} buckets covering {summary['count']} readings)",
            "",
            f"Overall: minimum {summary['min']} {unit}, average {summary['avg']} {unit}, "
            f"maximum {summary['max']} {unit}",
            f"Lowest point: {coldest['min']} {unit} at {_when(coldest['t'])}",
            f"Highest point: {hottest['max']} {unit} at {_when(hottest['t'])}",
            f"Most unsettled bucket: {noisiest['min']} to {noisiest['max']} {unit} "
            f"at {_when(noisiest['t'])}",
            filter_note,
            "",
            "Bucket averages across the range: "
            + ", ".join(f"{b['avg']:.2f}" for b in _thin(buckets, 40)),
            "",
            "In at most three short sentences: tell the operator what this "
            "particular view shows — the overall level and shape over this "
            "range, when anything notable happened, and whether the readings "
            "look normal. Mention the bucket size only if it matters to reading "
            "the curve.",
        ]
    )

    return _interpret("history", f"history:{signature}", signature, prompt, force)


def _thin(buckets: list[dict], limit: int) -> list[dict]:
    """
    Evenly sample the buckets down to `limit`.

    A month of hourly buckets is 700 numbers; pasting all of them into the
    prompt costs tokens and buys nothing, while an evenly spaced sample keeps
    the shape the model is being asked to describe.
    """

    if len(buckets) <= limit:
        return buckets

    step = len(buckets) / limit
    return [buckets[int(i * step)] for i in range(limit)]

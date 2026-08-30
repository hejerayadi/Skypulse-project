"""
The copilot: answers questions about SkyPulse and points at the answer.

Two things come back from one call. The prose goes in the chat; the `target`
is a place in the dashboard, which the frontend navigates to and highlights.
That is why the model is asked for JSON rather than text — the alternative is
parsing intent out of a sentence, which fails silently and often.

The model never writes a route. It picks an id from app/ai/platform_map.py and
the backend resolves it, so the worst a confused answer can do is send someone
to the wrong panel rather than to a page that does not exist.

A short summary of what the system is doing right now goes in every prompt, so
"is anything wrong?" is answerable without the operator having to go and look.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.ai import client
from app.ai.client import AIUnavailable
from app.ai.platform_map import SENSOR_IDS, TARGETS_BY_ID, catalogue, resolve
from app.rf_fault import stream as rf_stream
from app.services.telemetry_service import SENSOR_UNITS, get_sensor_series

# Long enough to follow a thread, short enough to keep the prompt cheap.
MAX_HISTORY = 12
MAX_MESSAGE_CHARS = 2000

SYSTEM_PROMPT = """You are the SkyPulse copilot, the in-app assistant for a CubeSat \
ground-station dashboard. You help operators understand the platform and their data.

You must reply with a single JSON object and nothing else:

{"answer": "...", "target_id": "<id or null>", "sensor": "<sensor id or null>", "guide": "<caption or null>"}

`answer` is what the operator reads. Write plain prose, two to five sentences, no \
markdown and no bullet points. Explain things properly — if they ask what something \
is, say what it is, what it shows and how to use it. Never invent a number: use only \
the live values given below, and if you do not have a figure, say so.

`target_id` is where in the dashboard the answer lives. Set it whenever the operator \
is asking about a feature, a chart, a control or a reading they could go and look at, \
so the app can take them there and highlight it. Set it to null for greetings, thanks, \
general questions about satellites, or anything with no place on screen. Only ever use \
an id from this list:

__CATALOGUE__

`sensor` is required when the target needs one, and must be one of: __SENSORS__. \
Pick the sensor the operator mentioned. If they did not mention one and the target \
needs one, choose the most relevant. Otherwise null.

`guide` is shown on the page itself, typed out beside the thing you pointed at, once \
the operator has been taken there. Required whenever you set a target, null otherwise. \
Write one or two short sentences in the imperative: what this panel is, and how to work \
it — "Pick a preset, or set From and To for an exact window; the bucket size follows \
automatically." Do not greet, do not refer to the question, do not repeat the answer \
word for word, and keep it under about 220 characters so it can be read at a glance.

When you set a target, write the answer as though the operator is about to be looking \
at it — "the date filter above the chart lets you…" rather than "go to the sensor \
page and find…". Do not tell them to navigate; the app does that for them."""


def _scene_state(prediction: dict) -> str:
    """
    How the 3D ground station is currently animating.

    Mirrors windIntensity()/thermalUrgency() in frontend/src/lib/rf-fault.ts: a
    nominal link leaves the scene completely still, and only the named fault
    animates. Kept in words rather than numbers because it is describing what
    the operator can see, not what the model measured.
    """

    label = prediction.get("label")
    severity = prediction.get("severity")

    if severity == "nominal":
        return "still — no beacon, no wind, nothing moving"

    if label == "overheating":
        eta = prediction.get("eta_seconds")
        pace = "flashing fast" if eta is not None and eta <= 30 else "flashing"
        return f"the red warning beacon beside the mast is {pace}; the antenna is not moving"

    if label == "wind":
        return "the dish is being pushed off aim and wind streaks are blowing across the scene"

    if label == "degraded":
        return "the warning beacon is flashing amber; the antenna is not moving"

    return "still"


def _live_state() -> str:
    """
    A compact picture of the system right now, for the prompt.

    Best effort throughout: the assistant is useful without it, so a store that
    is down costs a line of context rather than the whole answer.
    """

    lines: list[str] = []

    prediction = rf_stream.latest()
    if prediction and prediction.get("window_filled"):
        reading = prediction.get("reading", {})
        confidence = prediction.get("confidence") or 0
        detail = (
            f"RF link: {prediction['label']} ({prediction['severity']}), "
            f"confidence {confidence:.0%}"
        )
        if prediction.get("eta_seconds") is not None and prediction.get("eta_basis"):
            detail += f", forecast {prediction['eta_basis']} in ~{prediction['eta_seconds']:.0f}s"
        lines.append(detail)
        lines.append(
            f"  receiver {reading.get('sdr_temperature_c')} C, "
            f"offset {reading.get('frequency_offset_hz')} Hz, "
            f"wind {reading.get('wind_speed_mps')} m/s, "
            f"pointing error {reading.get('antenna_pointing_error_deg')} deg, "
            f"SNR {reading.get('snr')} dB"
        )
        # What the 3D view is literally doing, derived the same way the scene
        # derives it. Without this the model reasons from the raw wind figure
        # and explains a moving antenna that is in fact standing still — the
        # numbers are right and the answer is wrong.
        lines.append(f"  3D scene right now: {_scene_state(prediction)}")
    else:
        lines.append("RF link: no prediction yet (the model window is still filling).")

    for sensor_id in SENSOR_IDS:
        try:
            series = get_sensor_series(sensor_id=sensor_id, samples=12)
        except Exception:
            continue

        if not series.points:
            continue

        unit = series.unit or SENSOR_UNITS[sensor_id]
        lines.append(
            f"{sensor_id}: {series.latest:.2f} {unit} "
            f"(change over the last few readings {series.delta:+.2f})"
        )

    return "\n".join(lines) if lines else "No live readings available."


def _clean(messages: list[dict]) -> list[dict]:
    cleaned = []
    for message in messages[-MAX_HISTORY:]:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        content = str(message.get("content") or "").strip()[:MAX_MESSAGE_CHARS]
        if content:
            cleaned.append({"role": role, "content": content})
    return cleaned


def ask(messages: list[dict], current_route: str | None = None) -> dict:
    """
    Answer one turn of the conversation.

    Returns the reply plus a resolved target, or None for a question with no
    place on screen.
    """

    history = _clean(messages)
    if not history or history[-1]["role"] != "user":
        raise ValueError("The last message must be from the user.")

    # Substituted rather than .format()ed: the prompt contains a literal JSON
    # object, and str.format reads those braces as placeholders.
    system = SYSTEM_PROMPT.replace("__CATALOGUE__", catalogue()).replace(
        "__SENSORS__", ", ".join(SENSOR_IDS)
    )

    context = [
        f"Current time: {datetime.now(tz=timezone.utc).strftime('%d %b %Y %H:%M UTC')}",
        f"The operator is currently on: {current_route or 'unknown'}",
        "",
        "Live system state:",
        _live_state(),
    ]

    # The context rides on a system turn rather than being glued to the user's
    # message, so the model does not mistake it for something they typed.
    payload = [
        {"role": "system", "content": system},
        {"role": "system", "content": "\n".join(context)},
        *history,
    ]

    raw = client.complete_messages(payload, json_mode=True)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        # JSON mode makes this unlikely, but a malformed object should still
        # produce an answer rather than an error in the chat.
        print("Assistant returned non-JSON:", e)
        return {"reply": raw.strip(), "target": None}

    reply = str(parsed.get("answer") or "").strip()
    if not reply:
        raise AIUnavailable("The assistant returned an empty answer.")

    target = resolve(parsed.get("target_id"), parsed.get("sensor"))

    if target:
        # The registry description is the fallback, so a model that forgets the
        # field still produces a caption. It is written for a prompt rather than
        # for the screen, but a slightly bookish caption beats an empty callout
        # on a panel the reader was just sent to.
        guide = str(parsed.get("guide") or "").strip()
        target["guide"] = guide or TARGETS_BY_ID[target["id"]].description

    return {
        "reply": reply,
        "target": target,
        "model": client.MODEL,
    }

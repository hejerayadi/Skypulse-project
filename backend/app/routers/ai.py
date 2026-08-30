"""
AI interpretation endpoints.

Three panels, three questions: what is the RF link doing, what is this live
sensor trace doing, and what does the history range currently on screen show.

None of them are polled. A note is written when a page opens, when a chart's
range changes, or when the reader presses refresh — never on a timer. Between
those requests a shared server-side cache decides whether the question is
actually new (see app/ai/interpreter.py), so ten pages cost the same as one.

`force=true` is what the refresh button sends, and skips that cache.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.ai import assistant, interpreter
from app.ai.client import AIUnavailable

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/assistant")
async def ask_assistant(request: Request):
    """
    One turn of the in-app copilot.

    Body: {"messages": [{"role": "user"|"assistant", "content": "..."}],
           "current_route": "/dashboard/nodes"}

    Returns the reply plus, when the question is about something on screen, a
    `target` the frontend navigates to and highlights. The target is resolved
    from a fixed table on this side, so a confused model can pick the wrong
    panel but never an invalid route.
    """

    body = await request.json()

    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        raise HTTPException(status_code=400, detail="Expected {messages: [...]}")

    return await _run(
        assistant.ask,
        body["messages"],
        body.get("current_route"),
    )


@router.get("/status")
def get_status():
    """Whether interpretations are configured, and how much they have cost."""

    return interpreter.stats()


@router.get("/rf")
async def interpret_rf(force: bool = Query(False, description="Skip the cache.")):
    """One or two sentences on the ground station's current RF link state."""

    return await _run(interpreter.interpret_rf, force)


@router.get("/sensor/{sensor_id}/live")
async def interpret_live(
    sensor_id: str,
    force: bool = Query(False, description="Skip the cache."),
):
    """One or two sentences on what a sensor's live trace is doing."""

    return await _run(interpreter.interpret_sensor_live, sensor_id, force)


@router.get("/sensor/{sensor_id}/history")
async def interpret_history(
    sensor_id: str,
    start: datetime | None = Query(None),
    end: datetime | None = Query(None),
    granularity: str = Query("auto", pattern="^(auto|minute|hour|day|month|year)$"),
    force: bool = Query(False, description="Skip the cache."),
):
    """
    Two or three sentences on the history range currently on screen.

    Takes the same parameters as /api/telemetry/{sensor_id}/history, so the
    note always describes exactly the curve the user is looking at rather than
    some default range.
    """

    return await _run(
        interpreter.interpret_sensor_history,
        sensor_id,
        _as_utc(start),
        _as_utc(end),
        granularity,
        force,
    )


async def _run(function, *args):
    """
    Run one interpretation off the event loop.

    Both halves block: the Groq call is network I/O and the data it summarises
    comes from synchronous CouchDB reads.
    """

    try:
        return await run_in_threadpool(function, *args)
    except AIUnavailable as e:
        # 503, not 500: the dashboard works without this, and the panel should
        # say the note is unavailable rather than show an error.
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        # Nothing to interpret yet, or an unknown sensor.
        status = 404 if "Unknown sensor_id" in str(e) else 409
        raise HTTPException(status_code=status, detail=str(e))


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

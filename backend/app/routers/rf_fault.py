"""
RF fault detection endpoints.

The dashboard reads /api/rf/stream and nothing else in the steady state. The
rest exists so the feature can be driven and inspected without a browser:
/predict scores an arbitrary reading, /telemetry is the ingest a real feed
posts to, and /status says whether the model even loaded.
"""

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.rf_fault import stream
from app.rf_fault.detector import ModelUnavailable, detector

router = APIRouter(prefix="/rf", tags=["rf-fault"])


@router.get("/status")
def get_status():
    """Latest prediction plus the state of the pipeline behind it."""

    return {
        "latest": stream.latest(),
        "paused": stream.is_paused(),
        # What this model can actually report. Worth having next to `latest`:
        # after a retrain it is the quickest way to see whether a class you
        # expected made it into the deployed pickle.
        "labels": detector.labels,
        "threshold": detector.threshold,
        **stream.stats(),
    }


@router.get("/history")
def get_history(limit: int = Query(60, ge=1, le=180)):
    return {"predictions": stream.history(limit)}


@router.post("/telemetry")
async def post_telemetry(request: Request):
    """
    Ingest one RF reading from a live source and return its prediction.

    Posting here mutes the built-in simulator: real telemetry owns the window
    for as long as it keeps arriving. Scoring is a blocking model call, so it
    runs off the event loop.
    """

    reading = await request.json()

    if not isinstance(reading, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")

    try:
        return await run_in_threadpool(stream.ingest, reading, "live")
    except ModelUnavailable as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/predict")
async def post_predict(request: Request):
    """
    Same as /telemetry -- the reading still enters the window, because a
    window-based model has no way to score one in isolation. Kept as a separate
    path so "I am testing the model" reads differently in the logs from "this is
    the live feed".
    """

    return await post_telemetry(request)


@router.get("/stream")
async def get_stream():
    """
    Server-sent events. One frame per reading, newest state first on connect.
    """

    return StreamingResponse(
        stream.event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Nginx buffers text/event-stream by default, which turns a live
            # feed into a batch delivered whenever the buffer happens to flush.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/simulator/{action}")
def control_simulator(action: str):
    """Pause or resume the built-in simulator, for demoing a frozen state."""

    if action not in {"pause", "resume"}:
        raise HTTPException(status_code=404, detail="Expected 'pause' or 'resume'")

    stream.set_paused(action == "pause")
    return {"paused": stream.is_paused(), **stream.stats()}


@router.post("/reset")
def reset_window():
    """Clear the sliding window — the next 20 readings rebuild it."""

    detector.reset()
    return {"message": "Window cleared", "window_size": detector.window_size}

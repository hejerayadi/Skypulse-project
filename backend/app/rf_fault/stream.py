"""
The live layer: one reading in, one prediction out, fanned out to every browser.

Two things feed the detector and they must not interleave, because the model
scores a 20-step *window* -- mixing simulated and real readings inside one
window produces a discontinuity the model has never seen:

  * the built-in simulator thread, which ticks on a timer;
  * anything POSTing to /api/rf/telemetry -- the standalone feeder script, a
    Node-RED flow, or the real receiver when it exists.

Real telemetry wins. The moment an external reading arrives the simulator goes
quiet and the window is cleared; after RF_TAKEOVER_SECONDS of silence from that
source the simulator picks up again, clearing the window a second time. There is
never more than one source inside a window.

Fan-out is deliberately lossy at the edges: a browser that stops reading its
queue gets its oldest pending prediction dropped rather than growing the queue
without bound. A dashboard that missed a frame is fine; a backend that leaks
memory per stalled tab is not.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque

from app.rf_fault.detector import ModelUnavailable, detector
from app.rf_fault.simulator import RFSimulator

# Seconds between simulated readings. 2 s matches the notebook and is fast
# enough to watch a fault develop without the dashboard flickering.
SIM_INTERVAL = float(os.getenv("RF_SIM_INTERVAL", "2"))

# Default on: the whole point is that `uvicorn app.main:app` alone is enough to
# see the model driving the 3D view. Set false when a real feed is attached.
SIM_ENABLED = os.getenv("RF_SIM_ENABLED", "true").strip().lower() not in {"false", "0", "no"}

# How long a live source keeps the simulator muted after its last reading.
TAKEOVER_SECONDS = float(os.getenv("RF_TAKEOVER_SECONDS", "15"))

# Enough for the frontend to draw a few minutes of history on a fresh connect.
HISTORY_SIZE = 180

# Per-subscriber backlog. Small on purpose -- see the module docstring.
QUEUE_SIZE = 32

# Comment frames, so an idle connection is not reaped by a proxy mid-fault.
KEEPALIVE_SECONDS = 15.0

_lock = threading.Lock()
_subscribers: set[asyncio.Queue] = set()
_history: deque[dict] = deque(maxlen=HISTORY_SIZE)
_latest: dict | None = None

_loop: asyncio.AbstractEventLoop | None = None
_worker: threading.Thread | None = None
_stop = threading.Event()

_simulator = RFSimulator()
_sim_paused = False
_external_last_seen = 0.0
_active_source = "simulator"

_counters = {"simulated": 0, "live": 0, "rejected": 0}


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def start() -> None:
    """
    Load the model and start the simulator. Called from the FastAPI lifespan, so
    an event loop is already running and can be captured for the fan-out.
    """

    global _loop, _worker

    try:
        _loop = asyncio.get_running_loop()
    except RuntimeError:
        # Started outside a loop (a test, a script). Fan-out is then a no-op and
        # only the polling endpoints work, which is enough for those callers.
        _loop = None

    detector.load()

    if not SIM_ENABLED:
        print("RF_SIM_ENABLED is false - RF simulator off, /api/rf/telemetry still accepts readings.")
        return

    if not detector.available:
        print("RF simulator not started - the detector could not load.")
        return

    if _worker is None or not _worker.is_alive():
        _stop.clear()
        _worker = threading.Thread(target=_run_simulator, name="rf-simulator", daemon=True)
        _worker.start()
        print(f"RF simulator started - one reading every {SIM_INTERVAL:g}s.")


def stop(timeout: float = 5.0) -> None:
    _stop.set()
    if _worker is not None:
        _worker.join(timeout=timeout)


def stats() -> dict:
    return {
        "detector_available": detector.available,
        "detector_error": detector.load_error,
        "simulator_enabled": SIM_ENABLED,
        "simulator_running": _worker is not None and _worker.is_alive() and not _sim_paused,
        "simulator_muted_by_live_source": _muted(),
        "active_source": _active_source,
        "interval_seconds": SIM_INTERVAL,
        "subscribers": len(_subscribers),
        "history": len(_history),
        **_counters,
    }


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def ingest(reading: dict, source: str = "live") -> dict:
    """
    Score one reading and publish the result. Raises ModelUnavailable when the
    detector never loaded, and ValueError when the reading is incomplete -- the
    router turns both into a status code.
    """

    global _active_source, _external_last_seen

    if source != "simulator":
        _external_last_seen = time.monotonic()

    with _lock:
        # Clearing on a source change is what keeps a window from straddling two
        # different worlds. Cheap: the next 20 readings refill it.
        if source != _active_source:
            print(f"RF stream: source changed {_active_source} -> {source}, clearing the window.")
            detector.reset()
            _active_source = source

    try:
        prediction = detector.observe(reading, source=source)
    except ValueError:
        _counters["rejected"] += 1
        raise

    _counters["live" if source != "simulator" else "simulated"] += 1
    _publish(prediction)
    return prediction


def _muted() -> bool:
    """True while a live source has spoken recently enough to own the window."""

    return (time.monotonic() - _external_last_seen) < TAKEOVER_SECONDS


def _run_simulator() -> None:
    # wait() rather than sleep(): shutdown is immediate instead of taking up to
    # a full tick.
    while not _stop.wait(SIM_INTERVAL):
        if _sim_paused or _muted():
            continue

        try:
            ingest(_simulator.step(), source="simulator")
        except ModelUnavailable as e:
            print("RF simulator stopping -", e)
            return
        except Exception as e:
            # A bad tick must not end the stream for the life of the process.
            print("RF simulator tick failed:", e)


def set_paused(paused: bool) -> None:
    global _sim_paused
    _sim_paused = paused


def is_paused() -> bool:
    return _sim_paused


# ---------------------------------------------------------------------------
# Fan-out
# ---------------------------------------------------------------------------

def _publish(prediction: dict) -> None:
    global _latest

    with _lock:
        _latest = prediction
        _history.append(prediction)
        subscribers = list(_subscribers)

    if _loop is None or not subscribers:
        return

    # Queues belong to the event loop, and this runs on the simulator thread.
    _loop.call_soon_threadsafe(_deliver, prediction, subscribers)


def _deliver(prediction: dict, subscribers: list[asyncio.Queue]) -> None:
    for queue in subscribers:
        if queue.full():
            # Drop the oldest rather than the newest: a slow client should catch
            # up on the current state, not on a stale one.
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(prediction)
        except asyncio.QueueFull:
            pass


def latest() -> dict | None:
    with _lock:
        return _latest


def history(limit: int = 60) -> list[dict]:
    with _lock:
        return list(_history)[-limit:]


async def event_stream():
    """
    Server-sent events, one JSON prediction per frame.

    Opens with whatever the last prediction was, so a browser that connects
    mid-fault sees the alert immediately instead of waiting a tick for the next
    one.
    """

    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)

    with _lock:
        _subscribers.add(queue)
        snapshot = _latest

    try:
        # Tell EventSource how long to wait before reconnecting, so a backend
        # restart costs the dashboard three seconds rather than a page reload.
        yield "retry: 3000\n\n"

        if snapshot is not None:
            yield _frame(snapshot)

        while True:
            try:
                prediction = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue

            yield _frame(prediction)
    finally:
        # Runs on client disconnect too -- Starlette cancels the generator,
        # which raises into the await above.
        with _lock:
            _subscribers.discard(queue)


def _frame(prediction: dict) -> str:
    return f"data: {json.dumps(prediction)}\n\n"

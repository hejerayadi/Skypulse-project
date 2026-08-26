"""
MQTT ingest: Node-RED -> Mosquitto -> here.

Node-RED publishes each telemetry packet as JSON on skypulse/telemetry instead
of POSTing it. The payload is byte-for-byte what the HTTP endpoint used to
receive, so this module does exactly two things the HTTP layer did — decode the
JSON, hand it to ingest_service.ingest_packet() — and nothing else.

Two threads, on purpose:

  * paho's network loop, which owns the socket and answers the broker keepalive.
    It must never block.
  * one worker draining a bounded queue, which does the CouchDB and Atlas
    writes. Those take real time, and doing them inside on_message would stall
    PINGRESP until Mosquitto decided we were gone and dropped the session.

The subscription is a persistent one (clean_session=False plus a fixed client
id) at QoS 1, so packets Node-RED publishes while the backend is restarting are
queued by the broker and delivered when it comes back rather than lost.
"""

from __future__ import annotations

import json
import os
import queue
import threading

from dotenv import load_dotenv

from app.services.ingest_service import TelemetryRejected, ingest_packet

load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "skypulse/telemetry")
MQTT_QOS = int(os.getenv("MQTT_QOS", "1"))
MQTT_CLIENT_ID = os.getenv("MQTT_CLIENT_ID", "skypulse-backend")
MQTT_KEEPALIVE = int(os.getenv("MQTT_KEEPALIVE", "60"))

MQTT_USERNAME = os.getenv("MQTT_USERNAME") or None
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD") or None

# Set MQTT_ENABLED=false to run on HTTP ingest alone — useful when Mosquitto is
# not up, or when a second backend instance should not also consume the topic.
enabled = os.getenv("MQTT_ENABLED", "true").strip().lower() not in {"false", "0", "no"}

# Deep enough to ride out a slow CouchDB write at the ~7 packets/second the
# nodes emit; bounded so an unreachable store costs us memory we can name
# instead of all of it.
_QUEUE_MAXSIZE = 5000
_ENQUEUE_TIMEOUT = 0.5

_queue: queue.Queue = queue.Queue(maxsize=_QUEUE_MAXSIZE)
_stop = threading.Event()
_worker: threading.Thread | None = None

_client = None
_connected = False

# Counters for /api/storage, so you can tell "no data" apart from "no broker".
_received = 0
_stored = 0
_rejected = 0
_dropped = 0


def start() -> None:
    """Connect to the broker and start consuming. Never raises into the caller."""

    global _client, _worker

    if not enabled:
        print("MQTT_ENABLED is false — MQTT ingest disabled.")
        return

    try:
        import paho.mqtt.client as paho
    except ImportError:
        print(
            "paho-mqtt is not installed — MQTT ingest disabled. "
            "Run: pip install -r backend/requirements.txt"
        )
        return

    if _worker is None or not _worker.is_alive():
        _stop.clear()
        _worker = threading.Thread(target=_drain_forever, name="mqtt-ingest", daemon=True)
        _worker.start()

    # paho 2.x requires the callback API version; 1.x has no such argument.
    try:
        _client = paho.Client(
            callback_api_version=paho.CallbackAPIVersion.VERSION1,
            client_id=MQTT_CLIENT_ID,
            clean_session=False,
        )
    except (AttributeError, TypeError):
        _client = paho.Client(client_id=MQTT_CLIENT_ID, clean_session=False)

    if MQTT_USERNAME:
        _client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    _client.on_connect = _on_connect
    _client.on_disconnect = _on_disconnect
    _client.on_message = _on_message

    # Retry forever with backoff instead of giving up: a broker that is slow to
    # come up must not cost us the subscription for the life of the process.
    _client.reconnect_delay_set(min_delay=1, max_delay=30)

    try:
        # connect_async plus loop_start, so a broker that is down at boot does
        # not block the FastAPI lifespan — the network thread keeps retrying.
        _client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=MQTT_KEEPALIVE)
        _client.loop_start()
        print(f"MQTT ingest: connecting to {MQTT_HOST}:{MQTT_PORT}, topic {MQTT_TOPIC}")
    except Exception as e:
        print(f"Could not start MQTT ingest ({MQTT_HOST}:{MQTT_PORT}):", e)


def stop(timeout: float = 5.0) -> None:
    """Stop consuming, then let the worker flush what it already pulled down."""

    global _client

    if _client is not None:
        try:
            _client.loop_stop()
            _client.disconnect()
        except Exception as e:
            print("MQTT disconnect failed:", e)
        _client = None

    if _worker is not None:
        _stop.set()
        _worker.join(timeout=timeout)


def stats() -> dict:
    """What the subscriber has seen, for the /api/storage status endpoint."""

    if not enabled:
        return {"enabled": False, "connected": False}

    return {
        "enabled": True,
        "connected": _connected,
        "broker": f"{MQTT_HOST}:{MQTT_PORT}",
        "topic": MQTT_TOPIC,
        "received": _received,
        "stored": _stored,
        "rejected": _rejected,
        # Packets the broker delivered but a full queue could not accept. Any
        # number above zero means the stores are not keeping up with the nodes.
        "dropped": _dropped,
        "queue_depth": _queue.qsize(),
    }


# --------------------------------------------------------------------------
# paho callbacks — all of these run on the network thread. Keep them cheap.
# --------------------------------------------------------------------------

def _on_connect(client, userdata, flags, rc, properties=None) -> None:
    global _connected

    if rc != 0:
        _connected = False
        print(f"MQTT connection refused by {MQTT_HOST}:{MQTT_PORT} (code {rc})")
        return

    _connected = True

    # Subscribed here rather than once after connect_async: paho replays no
    # subscriptions of its own, so re-subscribing on every connect is what keeps
    # the topic live across a broker restart.
    client.subscribe(MQTT_TOPIC, qos=MQTT_QOS)
    print(f"MQTT ingest: subscribed to {MQTT_TOPIC} (qos {MQTT_QOS})")


def _on_disconnect(client, userdata, rc, properties=None, reason=None) -> None:
    global _connected

    _connected = False

    if rc != 0:
        # paho reconnects on its own from the network thread; this is a notice,
        # not something the caller has to act on.
        print(f"MQTT disconnected from {MQTT_HOST}:{MQTT_PORT} (code {rc}) — reconnecting.")


def _on_message(client, userdata, message) -> None:
    global _received, _rejected, _dropped

    _received += 1

    try:
        payload = json.loads(message.payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        _rejected += 1
        print(f"MQTT: dropping non-JSON payload on {message.topic}:", e)
        return

    if not isinstance(payload, dict):
        _rejected += 1
        print(f"MQTT: dropping non-object payload on {message.topic}")
        return

    try:
        # A brief block rather than put_nowait: a momentary write stall should
        # slow the network thread for half a second rather than lose the packet.
        # Past that we drop it — holding the loop longer risks the keepalive.
        _queue.put(payload, timeout=_ENQUEUE_TIMEOUT)
    except queue.Full:
        _dropped += 1
        print("MQTT ingest queue full — dropping packet.")


# --------------------------------------------------------------------------
# Worker thread — the blocking half.
# --------------------------------------------------------------------------

def _drain_forever() -> None:
    global _stored, _rejected

    # Keep draining after stop() is set, so shutdown is not lossy for packets
    # the broker already handed us.
    while not _stop.is_set() or not _queue.empty():
        try:
            payload = _queue.get(timeout=0.25)
        except queue.Empty:
            continue

        try:
            ingest_packet(payload, source="mqtt")
            _stored += 1
        except TelemetryRejected as e:
            _rejected += 1
            print("MQTT: rejected packet —", e.detail)
        except Exception as e:
            # CouchDBUnavailable lands here. Over HTTP that becomes a 503 the
            # publisher can see; over MQTT there is nobody to answer, so the
            # packet is logged and lost — but the worker has to survive it, or
            # one CouchDB blip ends ingest for the life of the process.
            _rejected += 1
            print("MQTT: failed to store packet —", e)

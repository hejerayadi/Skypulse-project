"""
The telemetry ingest pipeline, with no transport attached to it.

Packets reach the backend two ways now — Node-RED can POST them to
/api/telemetry, or publish them to Mosquitto and let app/mqtt.py pick them up —
and both have to land in exactly the same place: node registry, validation,
denoising, CouchDB, Atlas mirror. Keeping that sequence here rather than in the
HTTP handler means neither transport can quietly drift from the other.

Nothing in this module knows about HTTP. A packet we refuse raises
TelemetryRejected; the router turns that into a 4xx, the MQTT worker logs it and
moves to the next message.
"""

from __future__ import annotations

import copy

from pydantic_core import ValidationError

from app import mongo
from app.database import save_denoised_document, save_raw_document
from app.models.telemetry import (
    AcousticTelemetry,
    EnvironmentTelemetry,
    MotionTelemetry,
    SystemStatusTelemetry,
)
from app.services.denoising_service import apply_denoising
from app.services.node_service import record_node_state

# Maps each event_type discriminator to the model that should validate it,
# since neither transport hands us anything better than a plain dict.
MODEL_REGISTRY = {
    "environment": EnvironmentTelemetry,
    "motion": MotionTelemetry,
    "acoustic": AcousticTelemetry,
    "status": SystemStatusTelemetry,
}


class TelemetryRejected(Exception):
    """
    A packet we understood well enough to refuse: no event_type, an event_type
    we have no model for, or a body that fails validation.

    `status_code` is the HTTP status the router should answer with, kept here so
    the two transports agree on what counts as a bad packet without the ingest
    pipeline importing FastAPI.
    """

    def __init__(self, detail, status_code: int = 400):
        super().__init__(detail if isinstance(detail, str) else str(detail))
        self.detail = detail
        self.status_code = status_code


def ingest_packet(data: dict, source: str = "http") -> dict:
    """
    Store one telemetry packet, raw and denoised, in both stores.

    `source` names the transport the packet arrived on and only affects logging.
    Both paths are otherwise identical, so without it a terminal carrying both
    HTTP and MQTT ingest gives you no way to tell which one a packet came in on
    — or to notice that one of them has silently stopped delivering.

    Blocking throughout — CouchDB I/O is synchronous — so callers on an event
    loop must run this in a threadpool, and the MQTT subscriber runs it on its
    own worker thread rather than on paho's network loop.
    """

    print(f"Received [{source}]:", data)

    # Identity, GPS, battery and health only exist on the outer envelope, so the
    # fleet map has to be fed before we drop down into the sensor reading.
    # A failure here must not cost us the telemetry itself.
    try:
        record_node_state(data)
    except Exception as e:
        print("Failed to record node state:", e)

    # Node-RED sends telemetry under "sensors"
    if "sensors" in data:
        data = data["sensors"]

    event_type = data.get("event_type")

    if not event_type:
        raise TelemetryRejected("Missing event_type", status_code=400)

    model = MODEL_REGISTRY.get(event_type)

    if model is None:
        raise TelemetryRejected(f"Unknown event type: {event_type}", status_code=400)

    try:
        telemetry = model(**data)
    except ValidationError as e:
        raise TelemetryRejected(e.errors(), status_code=422)

    document = telemetry.model_dump(mode="json")

    # deepcopy, not .copy(): the readings live in nested dicts, so a shallow
    # copy shares them with `document` and the denoisers write their results
    # straight back into the raw packet — filtered_value would appear on it, and
    # motion's in-place accel_x/y/z rewrite would replace the raw reading
    # outright. get_sensor_history() then reads those denoised values back as if
    # they were raw and feeds the filter its own output.
    denoised_document = apply_denoising(copy.deepcopy(document))

    # Mirrored to Atlas first: these calls only queue, while the CouchDB saves
    # below stamp _id/_rev onto the documents in place.
    mongo.save_raw_document(document)
    mongo.save_preprocessed_document(denoised_document)

    save_raw_document(document)
    save_denoised_document(denoised_document)

    return {
        "message": "Telemetry stored successfully",
        "event_type": telemetry.event_type,
        "device_id": telemetry.device_id,
    }

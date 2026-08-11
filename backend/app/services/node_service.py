from __future__ import annotations

from datetime import datetime, timezone

from app.database import get_all_node_states, save_node_state
from app.models.telemetry import (
    Location,
    NodeBattery,
    NodeEnvelope,
    NodeFleet,
    NodeStatus,
)

# A node that stops reporting is offline no matter what its last packet claimed:
# silence is the strongest failure signal we have.
OFFLINE_AFTER_SECONDS = 30

LOW_BATTERY_PERCENTAGE = 20

DEGRADED_STATUSES = {"degraded", "warning", "warn", "unstable"}
FAILED_STATUSES = {"critical", "fault", "error", "failed", "offline", "down", "dead"}


def record_node_state(payload: dict) -> str | None:
    """
    Persist the node envelope wrapped around a telemetry packet.

    Returns the node id that was stored, or None when the payload carries no
    identity we can attribute it to.
    """

    sensors = payload.get("sensors")
    if not isinstance(sensors, dict):
        sensors = {}

    # Node-RED sends node_id on the envelope; a sensor posting directly to the
    # API only knows its own device_id, which is the same physical box.
    node_id = payload.get("node_id") or sensors.get("device_id")
    if not node_id:
        return None

    envelope = NodeEnvelope(
        node_id=node_id,
        timestamp=payload.get("timestamp") or sensors.get("timestamp") or datetime.now(tz=timezone.utc),
        location=payload.get("location"),
        battery=payload.get("battery"),
        health=payload.get("health"),
    )

    state = envelope.model_dump(mode="json")

    # timestamp is the device clock and can drift; received_at is ours and is
    # what staleness gets measured against.
    state["received_at"] = datetime.now(tz=timezone.utc).isoformat()
    state["last_event_type"] = sensors.get("event_type") or payload.get("event_type")

    save_node_state(node_id, state)

    return node_id


def get_fleet() -> NodeFleet:
    now = datetime.now(tz=timezone.utc)

    nodes = [_build_status(doc, now) for doc in get_all_node_states()]
    nodes = [node for node in nodes if node is not None]
    nodes.sort(key=lambda node: node.node_id)

    return NodeFleet(
        nodes=nodes,
        online=sum(1 for node in nodes if node.state == "online"),
        degraded=sum(1 for node in nodes if node.state == "degraded"),
        offline=sum(1 for node in nodes if node.state == "offline"),
        generated_at=now,
    )


def _build_status(doc: dict, now: datetime) -> NodeStatus | None:
    node_id = doc.get("node_id")
    if not node_id:
        return None

    last_seen = _parse_timestamp(doc.get("received_at")) or _parse_timestamp(doc.get("timestamp")) or now
    seconds_since_seen = max((now - last_seen).total_seconds(), 0.0)

    health = doc.get("health") or {}
    reported_status = str(health.get("status") or "unknown").lower()
    reported_online = health.get("online", True)

    battery = NodeBattery(**doc["battery"]) if doc.get("battery") else None
    location = Location(**doc["location"]) if doc.get("location") else None

    state, reason = _classify(
        reported_status=reported_status,
        reported_online=bool(reported_online),
        seconds_since_seen=seconds_since_seen,
        battery=battery,
    )

    return NodeStatus(
        node_id=node_id,
        state=state,
        reason=reason,
        reported_status=reported_status,
        last_seen=last_seen,
        seconds_since_seen=round(seconds_since_seen, 1),
        location=location,
        battery=battery,
        last_event_type=doc.get("last_event_type"),
    )


def _classify(
    reported_status: str,
    reported_online: bool,
    seconds_since_seen: float,
    battery: NodeBattery | None,
) -> tuple[str, str]:
    if seconds_since_seen > OFFLINE_AFTER_SECONDS:
        return "offline", f"No telemetry for {_humanize(seconds_since_seen)}"

    if not reported_online:
        return "offline", "Node reported itself offline"

    if reported_status in FAILED_STATUSES:
        return "offline", f"Node reported status '{reported_status}'"

    if reported_status in DEGRADED_STATUSES:
        return "degraded", f"Node reported status '{reported_status}'"

    if battery is not None and battery.percentage is not None:
        if battery.percentage <= LOW_BATTERY_PERCENTAGE and not battery.charging:
            return "degraded", f"Battery at {battery.percentage:.0f}%"

    return "online", "Reporting normally"


def _parse_timestamp(value) -> datetime | None:
    if not value:
        return None

    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None

    # CouchDB round-trips whatever the device sent, so naive stamps are assumed UTC.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def _humanize(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"

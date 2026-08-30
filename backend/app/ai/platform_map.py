"""
What the assistant is allowed to point at.

Every place in the dashboard the copilot can send someone is listed here, and
nowhere else. The model never writes a URL: it picks an `id` from this table
and the backend resolves it into a route, a parameter and a DOM anchor. A
hallucinated id resolves to nothing and the answer is delivered without a jump,
which is the failure mode we want — a wrong sentence is recoverable, a
navigation to a route that does not exist is a broken app.

`anchor` is the `data-tour` attribute on the element the frontend highlights.
Adding a destination means adding a row here and putting the matching
`data-tour` on the component; nothing else changes.
"""

from __future__ import annotations

from dataclasses import dataclass

# Kept in step with frontend/src/lib/sensors.ts. Used to validate the sensor a
# model picked, so `/dashboard/made-up` can never be navigated to.
SENSOR_IDS = ["temperature", "humidity", "pressure", "acoustic", "motion"]


@dataclass(frozen=True)
class Target:
    id: str
    label: str
    route: str
    anchor: str
    description: str
    # Routes with $sensor need one chosen before they can be navigated to.
    needs_sensor: bool = False


TARGETS: list[Target] = [
    Target(
        id="overview",
        label="Mission overview",
        route="/dashboard",
        anchor="overview-grid",
        description=(
            "The landing page. One card per sensor showing its current value and a "
            "sparkline; clicking a card opens that sensor's detail page."
        ),
    ),
    Target(
        id="sidebar",
        label="Navigation sidebar",
        route="/dashboard",
        anchor="app-sidebar",
        description=(
            "The left-hand navigation: overview, ground station, and a link per sensor. "
            "Also holds the sign-out button."
        ),
    ),
    Target(
        id="sensor-live",
        label="Live signal chart",
        route="/dashboard/$sensor",
        anchor="sensor-live-chart",
        needs_sensor=True,
        description=(
            "The live chart on a sensor page: the last 90 readings at roughly one per "
            "second, redrawn continuously. Shows the denoised value."
        ),
    ),
    Target(
        id="sensor-raw-overlay",
        label="Raw signal overlay",
        route="/dashboard/$sensor",
        anchor="sensor-live-chart",
        needs_sensor=True,
        description=(
            "Hovering either chart reveals a dashed amber trace: the reading before "
            "denoising. It shows what the filter removed. The tooltip then lists the "
            "denoised value, the raw value and the shift between them."
        ),
    ),
    Target(
        id="sensor-history",
        label="Stored history",
        route="/dashboard/$sensor",
        anchor="sensor-history",
        needs_sensor=True,
        description=(
            "The history panel below the live chart. Browses readings already recorded "
            "in the database over any date range, drawn as bucketed min/max/average "
            "rather than raw samples."
        ),
    ),
    Target(
        id="sensor-history-controls",
        label="History date filter",
        route="/dashboard/$sensor",
        anchor="history-controls",
        needs_sensor=True,
        description=(
            "The controls on the history panel: quick range presets (1h, 24h, 7d, 30d, "
            "All), From and To date-and-time pickers, and the bucket size "
            "(Auto/Minute/Hour/Day). Changing any of them re-queries and re-interprets."
        ),
    ),
    Target(
        id="sensor-history-export",
        label="CSV export",
        route="/dashboard/$sensor",
        anchor="history-export",
        needs_sensor=True,
        description=(
            "Downloads the history buckets currently on screen as CSV, including both "
            "the denoised and raw columns."
        ),
    ),
    Target(
        id="ground-station",
        label="3D ground station",
        route="/dashboard/nodes",
        anchor="station-3d",
        description=(
            "A 3D view of the ground station. The dish tracks whichever node last "
            "reported; each node is a pad with a mast and a status beacon. Drag to "
            "orbit, scroll to zoom."
        ),
    ),
    Target(
        id="rf-alert",
        label="RF fault panel",
        route="/dashboard/nodes",
        anchor="rf-alert-panel",
        description=(
            "The panel over the 3D view showing the RF fault model's verdict: the class "
            "it picked, its confidence, the full class probability split, an ETA for "
            "when the fault bites, and the readings driving it."
        ),
    ),
    Target(
        id="rf-visuals",
        label="Fault indicators in 3D",
        route="/dashboard/nodes",
        anchor="station-3d",
        description=(
            "How the 3D scene reacts to the fault model. An overheating receiver makes "
            "a red beacon flash on a post beside the mast, flashing faster as the ETA "
            "shortens. Wind pushes the dish off aim and blows streaks across the scene. "
            "A healthy link leaves the scene completely still."
        ),
    ),
    Target(
        id="site-health",
        label="Site health list",
        route="/dashboard/nodes",
        anchor="site-health",
        description=(
            "Below the 3D view: every node grouped by GPS site, with its state, battery, "
            "role and how long since it last reported."
        ),
    ),
]

TARGETS_BY_ID = {target.id: target for target in TARGETS}


def catalogue() -> str:
    """The destination list as the model sees it."""

    lines = []
    for target in TARGETS:
        suffix = " (needs a sensor)" if target.needs_sensor else ""
        lines.append(f"- {target.id}{suffix}: {target.description}")
    return "\n".join(lines)


def resolve(target_id: str | None, sensor: str | None) -> dict | None:
    """
    Turn what the model picked into something the frontend can navigate to.

    Returns None for anything unrecognised rather than raising: an answer with
    no jump is a fine outcome, and a made-up id should degrade to that quietly.
    """

    if not target_id:
        return None

    target = TARGETS_BY_ID.get(target_id.strip())
    if target is None:
        print(f"Assistant picked an unknown target id: {target_id!r} — ignoring.")
        return None

    params: dict[str, str] = {}

    if target.needs_sensor:
        chosen = (sensor or "").strip().lower()
        if chosen not in SENSOR_IDS:
            # The model asked for a sensor page without naming a valid sensor.
            # Temperature is the first card on the overview, so it is the least
            # surprising default — better than refusing to navigate at all.
            chosen = SENSOR_IDS[0]
        params["sensor"] = chosen

    return {
        "id": target.id,
        "label": target.label,
        "route": target.route,
        "params": params,
        "anchor": target.anchor,
    }

import type { NodeStatus } from "@/lib/nodes";

/**
 * Placement rules for the 3D ground station.
 *
 * On the map a node's position came from its GPS fix. There is no GPS here --
 * the station is a diagram, not a territory -- so a node's position is a slot
 * we assign it. Everything in this file is about making that assignment stable:
 * a node must not hop to a different corner of the pad between two polls.
 */

/** The antenna is normalised to this height, so every other constant below is
 *  expressed in units of "how tall the dish is" and survives a model swap. */
export const ANTENNA_HEIGHT = 10;

/** Radius of the inner ring of node pads, measured from the antenna's mast. */
const INNER_RING = 6.4;
const OUTER_RING = 9.2;

/**
 * Fixed pads, in draw order. The first four sit on the inner ring at the
 * diagonals -- off-axis so no node hides behind the mast from the default
 * camera, and so the four read as a square rather than a cross.
 *
 * Slots are a fixed table rather than `index / count * 2PI` on purpose: a
 * computed spread would re-place every surviving node the moment one dropped
 * off the fleet, which looks like the whole station rearranging itself.
 */
const SLOT_ANGLES = [
  Math.PI * 0.25,
  Math.PI * 0.75,
  Math.PI * 1.25,
  Math.PI * 1.75,
  Math.PI * 0.0,
  Math.PI * 0.5,
  Math.PI * 1.0,
  Math.PI * 1.5,
];

/**
 * Pin a node to a specific pad once you know the real hardware ids.
 *
 * Node ids are whatever the device sends (`node_service.record_node_state`
 * reads them off the payload), so they cannot be hardcoded ahead of time.
 * Until this is filled in, pads are handed out by sorted-id order, which is
 * stable for a fixed fleet -- the backend already sorts by node_id -- but will
 * shuffle if a node is permanently removed. Pin them here to freeze the layout.
 *
 * @example { "node-a1": 0, "node-b2": 1 }
 */
export const NODE_SLOT_OVERRIDES: Record<string, number> = {};

export type NodePlacement = {
  node: NodeStatus;
  /** World-space pad position, y = 0 (ground). */
  position: [number, number, number];
  /** Compass bearing of the pad, for pointing the dish at it. */
  azimuth: number;
  slot: number;
};

/**
 * The dish is a paraboloid, so it has to sit above the pads it talks to; the
 * beam is drawn to this point rather than to the model's bounding-box top.
 */
export const DISH_FOCUS: [number, number, number] = [0, ANTENNA_HEIGHT * 0.82, 0];

export function placeNodes(nodes: NodeStatus[]): NodePlacement[] {
  // Sorted so slot assignment does not depend on the order the API happened to
  // return, only on the identities present.
  const ordered = [...nodes].sort((a, b) => a.node_id.localeCompare(b.node_id));

  const taken = new Set<number>();
  for (const node of ordered) {
    const pinned = NODE_SLOT_OVERRIDES[node.node_id];
    if (pinned !== undefined) taken.add(pinned);
  }

  let next = 0;
  const claim = () => {
    while (taken.has(next)) next++;
    taken.add(next);
    return next;
  };

  return ordered.map((node) => {
    const slot = NODE_SLOT_OVERRIDES[node.node_id] ?? claim();
    const angle = SLOT_ANGLES[slot % SLOT_ANGLES.length];
    const radius = slot < 4 ? INNER_RING : OUTER_RING;

    return {
      node,
      position: [Math.cos(angle) * radius, 0, Math.sin(angle) * radius],
      azimuth: angle,
      slot,
    };
  });
}

/**
 * Shortest signed turn from `from` to `to` on a circle.
 *
 * Without this the dish takes the long way round whenever a slew crosses the
 * 2PI seam -- a 350deg spin to cover 10deg of sky.
 */
export function shortestTurn(from: number, to: number): number {
  const TAU = Math.PI * 2;
  return ((((to - from) % TAU) + TAU + Math.PI) % TAU) - Math.PI;
}

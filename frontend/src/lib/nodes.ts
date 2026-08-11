export type NodeState = "online" | "degraded" | "offline";

export type NodeLocation = {
  latitude: number;
  longitude: number;
};

export type NodeBattery = {
  percentage: number | null;
  charging: boolean;
};

export type NodeStatus = {
  node_id: string;
  state: NodeState;
  reason: string;
  reported_status: string;
  last_seen: string;
  seconds_since_seen: number;
  location: NodeLocation | null;
  battery: NodeBattery | null;
  last_event_type: string | null;
};

export type NodeFleet = {
  nodes: NodeStatus[];
  online: number;
  degraded: number;
  offline: number;
  generated_at: string;
};

// Marker colours are plain hex because Leaflet paints into an SVG layer it owns,
// outside the Tailwind theme, and needs a resolved value.
export const NODE_STATE_STYLE: Record<NodeState, { label: string; color: string }> = {
  online: { label: "Healthy", color: "#22c55e" },
  degraded: { label: "Degraded", color: "#f59e0b" },
  offline: { label: "Offline", color: "#ef4444" },
};

// What each node actually contributes, keyed by the event type it last sent.
// The node ids alone say nothing about the payload behind them.
export const NODE_ROLE: Record<string, string> = {
  environment: "Temperature · Humidity · Pressure",
  motion: "Accelerometer · Gyroscope",
  acoustic: "Sound level",
  status: "Heartbeat · Battery · RSSI",
};

export function nodeRole(node: NodeStatus): string {
  if (!node.last_event_type) return "No packet yet";
  return NODE_ROLE[node.last_event_type] ?? node.last_event_type;
}

export type NodeSite = {
  key: string;
  latitude: number;
  longitude: number;
  nodes: NodeStatus[];
  /** Worst state present at the site, so one failing node still colours it. */
  state: NodeState;
  online: number;
  degraded: number;
  offline: number;
  /** Freshest packet from anywhere on the site. */
  lastSeenSeconds: number;
};

// Nodes sharing a GPS fix are one physical installation, not separate sites —
// four boxes on one mast would otherwise stack into a single unreadable dot.
// 4dp is ~11 m, comfortably inside GPS noise but far below a real second site.
const SITE_PRECISION = 4;

const STATE_SEVERITY: Record<NodeState, number> = { online: 0, degraded: 1, offline: 2 };

export function groupNodesBySite(nodes: NodeStatus[]): NodeSite[] {
  const sites = new Map<string, NodeSite>();

  for (const node of nodes) {
    if (!node.location) continue;

    const { latitude, longitude } = node.location;
    const key = `${latitude.toFixed(SITE_PRECISION)},${longitude.toFixed(SITE_PRECISION)}`;

    let site = sites.get(key);
    if (!site) {
      site = {
        key,
        latitude,
        longitude,
        nodes: [],
        state: "online",
        online: 0,
        degraded: 0,
        offline: 0,
        lastSeenSeconds: Number.POSITIVE_INFINITY,
      };
      sites.set(key, site);
    }

    site.nodes.push(node);
    site[node.state] += 1;
    if (STATE_SEVERITY[node.state] > STATE_SEVERITY[site.state]) site.state = node.state;
    site.lastSeenSeconds = Math.min(site.lastSeenSeconds, node.seconds_since_seen);
  }

  for (const site of sites.values()) {
    site.nodes.sort((a, b) => a.node_id.localeCompare(b.node_id));
  }

  return [...sites.values()].sort((a, b) => a.key.localeCompare(b.key));
}

export const EMPTY_FLEET: NodeFleet = {
  nodes: [],
  online: 0,
  degraded: 0,
  offline: 0,
  generated_at: "",
};

export function formatAge(seconds: number): string {
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${Math.round(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function formatCoordinates(location: NodeLocation | null): string {
  if (!location) return "No GPS fix";
  const lat = `${Math.abs(location.latitude).toFixed(4)}° ${location.latitude >= 0 ? "N" : "S"}`;
  const lon = `${Math.abs(location.longitude).toFixed(4)}° ${location.longitude >= 0 ? "E" : "W"}`;
  return `${lat}, ${lon}`;
}

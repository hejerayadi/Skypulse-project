import { createFileRoute } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import { BatteryCharging, MapPin, Zap } from "lucide-react";
import { Station3D } from "@/components/station-3d";
import { AINote } from "@/components/ai-note";
import { useNodeFleet } from "@/hooks/use-node-fleet";
import { useAIInterpretation } from "@/hooks/use-ai-interpretation";
import { useRFFault } from "@/hooks/use-rf-fault";
import { RF_LABEL_STYLE } from "@/lib/rf-fault";
import {
  NODE_STATE_STYLE,
  formatAge,
  formatCoordinates,
  groupNodesBySite,
  nodeRole,
  type NodeSite,
  type NodeStatus,
} from "@/lib/nodes";

export const Route = createFileRoute("/_authenticated/dashboard/nodes")({
  component: NodesPage,
});

function NodesPage() {
  const { fleet, loading, error } = useNodeFleet(5000);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  const { prediction, connected, error: rfError } = useRFFault();

  // Keyed on the fault state rather than on the prediction object, which is a
  // new one every two seconds. A new label, severity or countdown band is a new
  // situation and gets a fresh reading; a wobbling probability is not.
  const situation = prediction
    ? `${prediction.label}|${prediction.severity}|${etaBand(prediction.eta_seconds)}`
    : "none";

  // Re-read when the link actually changes state, not on a timer. Refresh by
  // hand for anything in between.
  const rfNote = useAIInterpretation(prediction?.window_filled ? "/api/ai/rf" : null, situation);

  const sites = useMemo(() => groupNodesBySite(fleet.nodes), [fleet.nodes]);
  const unplaced = useMemo(() => fleet.nodes.filter((node) => !node.location), [fleet.nodes]);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Ground station</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            The dish tracks whichever node last reported, and reacts to the RF fault model: the
            beacon flashes on an overheating receiver, the antenna is pushed about by wind. Drag to
            orbit, scroll to zoom.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Count label="Healthy" value={fleet.online} color={NODE_STATE_STYLE.online.color} />
          <Count label="Degraded" value={fleet.degraded} color={NODE_STATE_STYLE.degraded.color} />
          <Count label="Offline" value={fleet.offline} color={NODE_STATE_STYLE.offline.color} />
        </div>
      </div>

      {error && (
        <div className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {error} — showing the last known fleet state.
        </div>
      )}

      <div
        className="rounded-2xl border border-border/60 bg-card p-3"
        style={{ boxShadow: "var(--shadow-elegant)" }}
      >
        <Station3D
          nodes={fleet.nodes}
          selectedNodeId={selectedNodeId}
          onSelect={setSelectedNodeId}
          prediction={prediction}
          connected={connected}
          rfError={rfError}
        />

        <AINote
          note={rfNote.note}
          loading={rfNote.loading}
          unavailable={rfNote.unavailable}
          pending={rfNote.pending}
          onReload={rfNote.reload}
          label={
            prediction && prediction.severity !== "nominal"
              ? `What is happening · ${RF_LABEL_STYLE[prediction.label].short}`
              : "What is happening"
          }
          accent={prediction ? RF_LABEL_STYLE[prediction.label].color : undefined}
        />
      </div>

      <div data-tour="site-health">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-medium">Site health</h2>
          <span className="text-xs text-muted-foreground">Refreshes every 5 s</span>
        </div>

        {fleet.nodes.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-border/60 px-6 py-12 text-center text-sm text-muted-foreground">
            {loading
              ? "Loading nodes…"
              : "No node has reported yet. Nodes appear here as soon as telemetry arrives."}
          </div>
        ) : (
          <div className="space-y-4">
            {sites.map((site) => (
              <SiteCard
                key={site.key}
                site={site}
                selectedNodeId={selectedNodeId}
                onSelect={setSelectedNodeId}
              />
            ))}

            {unplaced.length > 0 && (
              <div className="rounded-2xl border border-dashed border-border/60 px-4 py-3 text-xs text-muted-foreground">
                {/* The 3D station gives every node a pad whether or not it has a
                    fix, so these are listed as missing GPS, not as missing. */}
                {unplaced.length} node{unplaced.length === 1 ? "" : "s"} reporting without a GPS
                fix: {unplaced.map((node) => node.node_id).join(", ")}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Coarse countdown bands, so an ETA ticking down does not re-ask the model
 *  every two seconds for the same answer. */
function etaBand(eta: number | null): string {
  if (eta === null) return "none";
  if (eta <= 30) return "imminent";
  if (eta <= 120) return "soon";
  return "later";
}

function SiteCard({
  site,
  selectedNodeId,
  onSelect,
}: {
  site: NodeSite;
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  const style = NODE_STATE_STYLE[site.state];
  // A site highlights when the node picked in the 3D view lives here.
  const selected = site.nodes.some((node) => node.node_id === selectedNodeId);

  return (
    <section
      className={`rounded-2xl border bg-card transition-all ${
        selected ? "border-primary/70 ring-1 ring-primary/40" : "border-border/60"
      }`}
      style={{ boxShadow: "var(--shadow-elegant)" }}
    >
      <button
        type="button"
        onClick={() => onSelect(site.nodes[0].node_id)}
        className="flex w-full flex-wrap items-center justify-between gap-3 border-b border-border/60 px-5 py-4 text-left"
      >
        <div className="flex min-w-0 items-center gap-2">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full"
            style={{ background: style.color, boxShadow: `0 0 0 3px ${style.color}25` }}
          />
          <div className="min-w-0">
            <div className="truncate text-sm font-medium">
              {site.nodes.length} node{site.nodes.length === 1 ? "" : "s"} at this site
            </div>
            <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <MapPin className="h-3 w-3 shrink-0" />
              <span className="truncate">{formatCoordinates(site.nodes[0].location)}</span>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {site.online > 0 && <Pill state="online" count={site.online} />}
          {site.degraded > 0 && <Pill state="degraded" count={site.degraded} />}
          {site.offline > 0 && <Pill state="offline" count={site.offline} />}
        </div>
      </button>

      <ul className="divide-y divide-border/40">
        {site.nodes.map((node) => (
          <NodeRow
            key={node.node_id}
            node={node}
            selected={node.node_id === selectedNodeId}
            onSelect={() => onSelect(node.node_id)}
          />
        ))}
      </ul>
    </section>
  );
}

function NodeRow({
  node,
  selected,
  onSelect,
}: {
  node: NodeStatus;
  selected: boolean;
  onSelect: () => void;
}) {
  const style = NODE_STATE_STYLE[node.state];
  const battery = node.battery?.percentage ?? null;

  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={selected}
        className={`flex w-full flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3 text-left transition-colors ${
          selected ? "bg-primary/10" : "hover:bg-muted/40"
        }`}
      >
        <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: style.color }} />

        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium">{node.node_id}</div>
          {/* What this node actually contributes — the id alone says nothing. */}
          <div className="truncate text-[11px] text-muted-foreground">{nodeRole(node)}</div>
        </div>

        <div className="text-right text-[11px] text-muted-foreground">
          <div style={{ color: style.color }}>{style.label}</div>
          <div className="tabular-nums">{formatAge(node.seconds_since_seen)}</div>
        </div>

        <div className="flex w-24 shrink-0 items-center gap-1.5">
          {node.battery?.charging ? (
            <BatteryCharging className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          ) : (
            <Zap className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          )}
          {battery === null ? (
            <span className="text-[11px] text-muted-foreground">—</span>
          ) : (
            <>
              <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.max(0, Math.min(100, battery))}%`,
                    background: style.color,
                  }}
                />
              </div>
              <span className="text-[11px] tabular-nums text-muted-foreground">
                {battery.toFixed(0)}%
              </span>
            </>
          )}
        </div>
      </button>
    </li>
  );
}

function Pill({ state, count }: { state: keyof typeof NODE_STATE_STYLE; count: number }) {
  const style = NODE_STATE_STYLE[state];
  return (
    <span
      className="rounded-full px-2 py-0.5 text-[11px] font-medium"
      style={{ background: `${style.color}1f`, color: style.color }}
    >
      {count} {style.label}
    </span>
  );
}

function Count({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center gap-2 rounded-xl border border-border/60 bg-card px-3 py-2">
      <span className="h-2 w-2 rounded-full" style={{ background: color }} />
      <span className="text-sm font-semibold tabular-nums">{value}</span>
      <span className="text-[11px] text-muted-foreground">{label}</span>
    </div>
  );
}

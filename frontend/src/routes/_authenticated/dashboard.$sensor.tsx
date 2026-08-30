import { createFileRoute, notFound, Link } from "@tanstack/react-router";
import { useState } from "react";
import { getSensor } from "@/lib/sensors";
import { useSensorStream } from "@/hooks/use-sensor-stream";
import { useAIInterpretation } from "@/hooks/use-ai-interpretation";
import { AINote } from "@/components/ai-note";
import {
  ResponsiveContainer,
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import { Button } from "@/components/ui/button";
import { SensorHistoryPanel } from "@/components/sensor-history";
import { DENOISERS, RAW_COLOR, RAW_DASH } from "@/lib/denoising";

export const Route = createFileRoute("/_authenticated/dashboard/$sensor")({
  loader: ({ params }) => {
    const sensor = getSensor(params.sensor);
    if (!sensor) throw notFound();
    return { sensor };
  },
  component: SensorPage,
  notFoundComponent: () => (
    <div className="mx-auto max-w-2xl py-16 text-center">
      <p className="text-sm text-muted-foreground">Unknown sensor.</p>
      <Button asChild className="mt-4" variant="outline">
        <Link to="/dashboard">Back to overview</Link>
      </Button>
    </div>
  ),
});

function SensorPage() {
  const { sensor } = Route.useLoaderData();
  const { points, latest, delta } = useSensorStream(sensor, 90, 1000);
  // Same contract as the history chart below: the raw trace is revealed on
  // hover rather than drawn all the time.
  const [showRaw, setShowRaw] = useState(false);
  const Icon = sensor.icon;

  // Read once when the page opens, then only when asked. A trace that redraws
  // every second has no moment that is the right one to re-narrate, so the
  // reader picks it with the refresh button.
  const liveNote = useAIInterpretation(`/api/ai/sensor/${sensor.id}/live`, sensor.id);

  const min = Math.min(...points.map((p) => p.v));
  const max = Math.max(...points.map((p) => p.v));
  const avg = points.reduce((a, b) => a + b.v, 0) / points.length;
  const gradId = `grad-detail-${sensor.id}`;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <div
            className="grid h-11 w-11 place-items-center rounded-xl"
            style={{ background: "var(--gradient-primary)" }}
          >
            <Icon className="h-5 w-5 text-primary-foreground" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">{sensor.name}</h1>
            <p className="text-sm text-muted-foreground">{sensor.description}</p>
          </div>
        </div>
        <div className="text-right">
          <div className="text-3xl font-semibold tabular-nums">
            {latest.toFixed(2)}
            <span className="ml-1 text-sm text-muted-foreground">{sensor.unit}</span>
          </div>
          <div className="text-xs text-muted-foreground">
            Δ {delta >= 0 ? "+" : ""}
            {delta.toFixed(2)} {sensor.unit}
          </div>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <Stat label="Min (90s)" value={`${min.toFixed(2)} ${sensor.unit}`} />
        <Stat label="Average" value={`${avg.toFixed(2)} ${sensor.unit}`} />
        <Stat label="Max (90s)" value={`${max.toFixed(2)} ${sensor.unit}`} />
      </div>

      <div
        data-tour="sensor-live-chart"
        className="rounded-2xl border border-border/60 bg-card p-5"
        style={{ boxShadow: "var(--shadow-elegant)" }}
      >
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <div className="text-sm font-medium">Live signal</div>
          <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <span
                className="h-0 w-4 border-t-2 border-dashed"
                style={{ borderColor: RAW_COLOR, opacity: showRaw ? 1 : 0.45 }}
              />
              Raw{showRaw ? "" : " · hover to reveal"}
            </span>
            <span className="hidden sm:inline">Last 90 samples · 1 s cadence</span>
          </div>
        </div>
        <div
          className="h-64"
          onMouseEnter={() => setShowRaw(true)}
          onMouseLeave={() => setShowRaw(false)}
        >
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.4} />
                  <stop offset="100%" stopColor="var(--primary)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis
                dataKey="t"
                tickFormatter={(t) =>
                  new Date(t).toLocaleTimeString([], { minute: "2-digit", second: "2-digit" })
                }
                stroke="var(--muted-foreground)"
                fontSize={11}
                tickLine={false}
                axisLine={false}
                minTickGap={40}
              />
              <YAxis
                domain={[sensor.min, sensor.max]}
                stroke="var(--muted-foreground)"
                fontSize={11}
                tickLine={false}
                axisLine={false}
                width={40}
              />
              <Tooltip
                contentStyle={{
                  background: "var(--popover)",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  fontSize: 12,
                }}
                labelFormatter={(t) => new Date(t as number).toLocaleTimeString()}
                formatter={(v: number, key) => [
                  `${v.toFixed(2)} ${sensor.unit}`,
                  key === "raw" ? "Raw" : "Denoised",
                ]}
              />
              <Area
                type="monotone"
                dataKey="v"
                stroke="var(--primary)"
                strokeWidth={2}
                fill={`url(#${gradId})`}
                isAnimationActive={false}
              />
              {/* The reading before denoising. Faded rather than unmounted, so
                  revealing it does not restart the chart's layout on a series
                  that redraws every second. */}
              <Line
                type="monotone"
                dataKey="raw"
                stroke={RAW_COLOR}
                strokeWidth={1.5}
                strokeDasharray={RAW_DASH}
                strokeOpacity={showRaw ? 0.95 : 0}
                dot={false}
                activeDot={false}
                isAnimationActive={false}
                style={{ transition: "stroke-opacity 180ms ease-out" }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <p className="mt-3 text-[11px] text-muted-foreground">
          {DENOISERS[sensor.id].detail
            ? `Denoised with a ${DENOISERS[sensor.id].name.toLowerCase()} · ${DENOISERS[sensor.id].detail}`
            : "No denoiser for this sensor yet — the stored value is the raw reading."}
        </p>

        <AINote
          note={liveNote.note}
          loading={liveNote.loading}
          unavailable={liveNote.unavailable}
          pending={liveNote.pending}
          onReload={liveNote.reload}
          label="Live reading"
        />
      </div>

      <SensorHistoryPanel sensor={sensor} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div
      className="rounded-xl border border-border/60 bg-card p-4"
      style={{ boxShadow: "var(--shadow-elegant)" }}
    >
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}

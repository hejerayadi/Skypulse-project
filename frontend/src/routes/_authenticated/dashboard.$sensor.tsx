import { createFileRoute, notFound, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useServerFn } from "@tanstack/react-start";
import { getSensor } from "@/lib/sensors";
import { useSensorStream } from "@/hooks/use-sensor-stream";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";
import { Sparkles, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { interpretSensor } from "@/lib/ai.functions";

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
  const [interpretation, setInterpretation] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const interpret = useServerFn(interpretSensor);
  const Icon = sensor.icon;

  async function runInterpretation() {
    setLoading(true);
    try {
      const res = await interpret({
        data: {
          sensor: sensor.name,
          unit: sensor.unit,
          recent: points.slice(-20).map((p) => p.v),
        },
      });
      setInterpretation(res.interpretation);
    } catch (err) {
      setInterpretation(err instanceof Error ? err.message : "Failed to interpret");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    runInterpretation();
    const id = setInterval(runInterpretation, 30000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sensor.id]);

  const min = Math.min(...points.map((p) => p.v));
  const max = Math.max(...points.map((p) => p.v));
  const avg = points.reduce((a, b) => a + b.v, 0) / points.length;
  const gradId = `grad-detail-${sensor.id}`;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-xl" style={{ background: "var(--gradient-primary)" }}>
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

      <div className="rounded-2xl border border-border/60 bg-card p-5" style={{ boxShadow: "var(--shadow-elegant)" }}>
        <div className="mb-4 flex items-center justify-between">
          <div className="text-sm font-medium">Live signal</div>
          <div className="text-xs text-muted-foreground">Last 90 samples · 1 s cadence</div>
        </div>
        <div className="h-64">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.4} />
                  <stop offset="100%" stopColor="var(--primary)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis
                dataKey="t"
                tickFormatter={(t) => new Date(t).toLocaleTimeString([], { minute: "2-digit", second: "2-digit" })}
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
                formatter={(v: number) => [`${v.toFixed(2)} ${sensor.unit}`, sensor.name]}
              />
              <Area type="monotone" dataKey="v" stroke="var(--primary)" strokeWidth={2} fill={`url(#${gradId})`} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="rounded-2xl border border-border/60 bg-card p-5" style={{ boxShadow: "var(--shadow-elegant)" }}>
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Sparkles className="h-4 w-4 text-primary" />
            AI interpretation
          </div>
          <Button variant="ghost" size="sm" onClick={runInterpretation} disabled={loading}>
            {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            <span className="ml-2">Refresh</span>
          </Button>
        </div>
        <p className="text-sm leading-relaxed text-foreground/90">
          {loading && !interpretation ? "Analyzing recent variation…" : interpretation || "No interpretation yet."}
        </p>
        <div className="mt-3 text-[11px] text-muted-foreground">
          Refreshes automatically every 30 seconds.
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-card p-4" style={{ boxShadow: "var(--shadow-elegant)" }}>
      <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{value}</div>
    </div>
  );
}
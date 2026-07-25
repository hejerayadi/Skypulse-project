import { useSensorStream } from "@/hooks/use-sensor-stream";
import type { SensorMeta } from "@/lib/sensors";
import { ResponsiveContainer, AreaChart, Area } from "recharts";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";

export function SensorMiniCard({ sensor }: { sensor: SensorMeta }) {
  const { points, latest, delta } = useSensorStream(sensor, 40, 1500);
  const Icon = sensor.icon;
  const up = delta >= 0;
  const gradId = `grad-${sensor.id}`;

  return (
    <div
      className="rounded-2xl border border-border/60 bg-card p-5 transition-all hover:border-primary/50"
      style={{ boxShadow: "var(--shadow-elegant)" }}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-accent text-accent-foreground">
            <Icon className="h-4 w-4" />
          </div>
          <div>
            <div className="text-sm font-medium">{sensor.name}</div>
            <div className="text-[11px] text-muted-foreground">{sensor.description.split(".")[0]}</div>
          </div>
        </div>
        <div className={`flex items-center gap-1 text-xs ${up ? "text-primary" : "text-muted-foreground"}`}>
          {up ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
          {Math.abs(delta).toFixed(2)}
        </div>
      </div>
      <div className="mt-4 flex items-baseline gap-1">
        <span className="text-3xl font-semibold tracking-tight tabular-nums">{latest.toFixed(1)}</span>
        <span className="text-sm text-muted-foreground">{sensor.unit}</span>
      </div>
      <div className="mt-2 h-14">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={points} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.5} />
                <stop offset="100%" stopColor="var(--primary)" stopOpacity={0} />
              </linearGradient>
            </defs>
            <Area type="monotone" dataKey="v" stroke="var(--primary)" strokeWidth={2} fill={`url(#${gradId})`} isAnimationActive={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
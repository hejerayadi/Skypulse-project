import { AlertTriangle, Radio, ShieldCheck, Wind } from "lucide-react";
import {
  RF_LABEL_STYLE,
  RF_SEVERITY_LABEL,
  classBreakdown,
  formatConfidence,
  formatEta,
  formatTrend,
  isAmbiguous,
  topDrivers,
  type RFPrediction,
} from "@/lib/rf-fault";

/**
 * The read-out for the RF fault model, laid over the 3D view.
 *
 * The scene itself carries the alarm — a flashing lamp, a dish being pushed
 * about — because that is what you notice from across a room. This panel is
 * what you read once it has your attention: the number behind the alarm, the
 * deadline, and which readings produced both.
 *
 * The full class distribution is shown rather than just the winner. A
 * three-class model that split 0.48/0.46/0.06 reached the same label as one
 * that split 0.99/0.01/0.00, and those are not the same situation.
 */

const LABEL_ICON = {
  normal: ShieldCheck,
  overheating: AlertTriangle,
  wind: Wind,
  degraded: AlertTriangle,
} as const;

export function RFAlertPanel({
  prediction,
  connected,
  error,
}: {
  prediction: RFPrediction | null;
  connected: boolean;
  error: string | null;
}) {
  if (!prediction) {
    return (
      <Shell>
        <div className="flex items-center gap-2 text-muted-foreground">
          <Radio className="h-3.5 w-3.5" />
          {error ?? "Waiting for the RF fault detector…"}
        </div>
      </Shell>
    );
  }

  if (!prediction.window_filled) {
    return (
      <Shell>
        <div className="flex items-center gap-2 text-muted-foreground">
          <Radio className="h-3.5 w-3.5 animate-pulse" />
          Filling the model window — {prediction.window_progress}/{prediction.window_size} readings
        </div>
      </Shell>
    );
  }

  const style = RF_LABEL_STYLE[prediction.label];
  const Icon = LABEL_ICON[prediction.label];
  const alerting = prediction.severity !== "nominal";
  const drivers = topDrivers(prediction);
  const breakdown = classBreakdown(prediction);
  const ambiguous = isAmbiguous(prediction);

  return (
    <Shell accent={alerting ? style.color : undefined}>
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 shrink-0" style={{ color: style.color }} />
        <span className="font-semibold" style={{ color: style.color }}>
          {style.title}
        </span>
        <span
          className="ml-auto rounded-full px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide"
          style={{ background: `${style.color}22`, color: style.color }}
        >
          {RF_SEVERITY_LABEL[prediction.severity]}
        </span>
      </div>

      <p className="mt-1.5 text-[11px] leading-snug text-muted-foreground">{style.hint}</p>

      <Meter
        label={`Model confidence · ${style.short.toLowerCase()}`}
        value={prediction.confidence ?? 0}
        color={style.color}
      />

      {ambiguous && (
        <p className="mt-1 text-[10px] leading-snug text-amber-500">
          Close call — the runner-up class is within {formatConfidence(prediction.margin)} of this
          one.
        </p>
      )}

      {alerting && (
        <>
          <div className="mt-2 flex items-baseline justify-between gap-3">
            <span className="text-[11px] text-muted-foreground">Expected</span>
            <span className="text-[13px] font-semibold" style={{ color: style.color }}>
              {formatEta(prediction.eta_seconds)}
            </span>
          </div>
          {prediction.eta_basis && (
            <p className="text-[10px] leading-snug text-muted-foreground">
              Extrapolating {prediction.eta_basis}.
            </p>
          )}
        </>
      )}

      {breakdown.length > 1 && (
        <div className="mt-2 border-t border-border/50 pt-2">
          <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
            Class probabilities
          </div>
          {/* Every class, not just the winner — how the model split its belief
              is the difference between a verdict and a guess. */}
          {breakdown.map((entry) => (
            <div key={entry.label} className="flex items-center gap-1.5">
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full"
                style={{ background: RF_LABEL_STYLE[entry.label].color }}
              />
              <span className="w-[4.5rem] shrink-0 truncate text-[10px] text-muted-foreground">
                {RF_LABEL_STYLE[entry.label].short}
              </span>
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-muted">
                <div
                  className="h-full rounded-full transition-[width] duration-500"
                  style={{
                    width: `${Math.round(entry.probability * 100)}%`,
                    background: RF_LABEL_STYLE[entry.label].color,
                    opacity: entry.label === prediction.label ? 1 : 0.45,
                  }}
                />
              </div>
              <span className="w-7 shrink-0 text-right text-[10px] tabular-nums text-muted-foreground">
                {Math.round(entry.probability * 100)}
              </span>
            </div>
          ))}
        </div>
      )}

      {drivers.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-border/50 pt-2">
          {drivers.map((driver) => (
            <li key={driver.key} className="flex items-baseline justify-between gap-2 text-[11px]">
              <span className="text-muted-foreground">{driver.label}</span>
              <span className="tabular-nums">
                {driver.value.toFixed(driver.unit === "Hz" ? 0 : 1)} {driver.unit}
                <span className="ml-1.5 opacity-60">{formatTrend(driver)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-2 flex items-center gap-1.5 border-t border-border/50 pt-1.5 text-[10px] text-muted-foreground">
        <span
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: connected ? "#22c55e" : "#f59e0b" }}
        />
        {connected ? "Live stream" : "Polling — stream down"}
        <span className="opacity-50">·</span>
        {prediction.source === "simulator" ? "simulated feed" : "live feed"}
        {/* Ground truth, and only the simulator has any. Labelled as the
            scenario rather than as anything the model produced. */}
        {prediction.scenario && (
          <>
            <span className="opacity-50">·</span>
            <span className="opacity-70">scenario: {prediction.scenario}</span>
          </>
        )}
      </div>
    </Shell>
  );
}

function Shell({ children, accent }: { children: React.ReactNode; accent?: string }) {
  return (
    <div
      data-tour="rf-alert-panel"
      className="pointer-events-none absolute left-2.5 top-2.5 w-[15.5rem] rounded-xl border bg-background/88 px-3 py-2.5 text-xs backdrop-blur-md transition-colors"
      style={{
        borderColor: accent ? `${accent}80` : undefined,
        boxShadow: accent ? `0 0 24px ${accent}33` : undefined,
      }}
    >
      {children}
    </div>
  );
}

function Meter({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="mt-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] text-muted-foreground">{label}</span>
        <span className="text-[11px] font-medium tabular-nums">{formatConfidence(value)}</span>
      </div>
      <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{
            width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%`,
            background: color,
          }}
        />
      </div>
    </div>
  );
}

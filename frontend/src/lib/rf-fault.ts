/**
 * The RF fault prediction the backend streams, and how the UI reads it.
 *
 * The model is multiclass — NORMAL, SDR_OVERHEATING, ANTENNA_WIND — so `label`
 * and every probability here come straight out of it. The one thing it does not
 * produce is `eta_seconds`: the classifier scores the present, and the deadline
 * is extrapolated from the window's trend on the backend.
 *
 * `degraded` is not a class the current model emits. It is the landing spot for
 * a class a retrained model adds that the UI has no specific treatment for, so
 * an unknown fault still shows as an alert instead of vanishing.
 */

export type RFLabel = "normal" | "overheating" | "wind" | "degraded";

/** How loudly to react: nothing, a pre-warning, or a live fault. */
export type RFSeverity = "nominal" | "watch" | "alert";

export type RFDriver = {
  key: string;
  label: string;
  unit: string;
  value: number;
  critical: number;
  /** 0 while unremarkable, 1 once it is at the critical threshold. */
  severity: number;
  /** Units per second, from a least-squares fit over the window. */
  trend: number;
  cause: "overheating" | "wind";
};

export type RFReading = {
  timestamp?: string;
  scenario?: string | null;
  sdr_temperature_c: number;
  wind_speed_mps: number;
  antenna_pointing_error_deg: number;
  rssi?: number;
  snr: number;
  ber: number;
  signal_lock: number;
  frequency_offset_hz: number;
};

export type RFPrediction = {
  timestamp: string;
  source: string;
  /** Ground truth from the simulator. Null on real telemetry — never a prediction. */
  scenario: string | null;
  label: RFLabel;
  severity: RFSeverity;
  /** Everything the model does not consider healthy — what `threshold` applies to. */
  fault_probability: number | null;
  /** The model's probability for the label actually being shown. */
  confidence: number | null;
  /** How far the winning class beat the runner-up. Low means a coin toss. */
  margin: number | null;
  /** One entry per class the model can emit. Empty while the window fills. */
  class_probabilities: Partial<Record<RFLabel, number>>;
  threshold: number;
  eta_seconds: number | null;
  eta_basis: string | null;
  eta_trend: number | null;
  eta_trend_unit: string | null;
  drivers: RFDriver[];
  window_size: number;
  window_filled: boolean;
  window_progress: number;
  samples_seen: number;
  reading: RFReading;
};

export const RF_LABEL_STYLE: Record<
  RFLabel,
  { title: string; short: string; color: string; hint: string }
> = {
  normal: {
    title: "Link nominal",
    short: "Nominal",
    color: "#22c55e",
    hint: "No impairment developing.",
  },
  overheating: {
    title: "SDR overheating",
    short: "Overheating",
    color: "#ef4444",
    hint: "The receiver is heat-soaking and its oscillator is drifting off the carrier.",
  },
  wind: {
    title: "Wind loading the antenna",
    short: "Wind",
    color: "#38bdf8",
    hint: "Gusts are walking the dish off boresight — expect the link margin to move.",
  },
  degraded: {
    title: "Link degraded",
    short: "Degraded",
    color: "#f59e0b",
    hint: "The model sees a fault that neither temperature nor wind explains.",
  },
};

export const RF_SEVERITY_LABEL: Record<RFSeverity, string> = {
  nominal: "Nominal",
  watch: "Watch",
  alert: "Alert",
};

export function formatEta(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "not trending";
  if (seconds <= 1) return "happening now";
  if (seconds < 60) return `in ~${Math.round(seconds)} s`;

  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest === 0 ? `in ~${minutes} min` : `in ~${minutes} min ${rest} s`;
}

export function formatConfidence(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

/** How far a named reading has travelled from quiet towards critical, 0–1. */
function driverSeverity(prediction: RFPrediction, key: string): number {
  return prediction.drivers.find((driver) => driver.key === key)?.severity ?? 0;
}

/**
 * How hard to blow the antenna about, 0–1.
 *
 * Split on purpose: the *model* decides whether wind is happening, the
 * *readings* decide how hard it blows. Using the class probability for both
 * would look wrong — it saturates near 1 the moment the model is sure, so a
 * light breeze and a gale would shake the dish identically.
 *
 * A `watch` is a real signal — the gust front has arrived, the classifier just
 * has not committed yet — so it still moves the dish, at roughly half strength.
 * Anything else is dead still: an idle scene that sways anyway would teach the
 * operator to ignore the one that matters.
 */
export function windIntensity(prediction: RFPrediction | null): number {
  if (!prediction || prediction.label !== "wind") return 0;

  const physical = Math.max(
    driverSeverity(prediction, "wind_speed_mps"),
    driverSeverity(prediction, "antenna_pointing_error_deg"),
  );

  // Floored, so a fault the model has called always visibly moves something
  // even when the gust itself is still building.
  const strength = Math.min(1, Math.max(physical, 0.3));

  if (prediction.severity === "alert") return strength;
  if (prediction.severity === "watch") return strength * 0.55;
  return 0;
}

/**
 * True when the top two classes are close enough that the call is a coin toss.
 * Worth surfacing rather than presenting an ambiguous prediction as a verdict.
 */
export function isAmbiguous(prediction: RFPrediction | null): boolean {
  return (
    prediction !== null &&
    prediction.severity !== "nominal" &&
    prediction.margin !== null &&
    prediction.margin < 0.2
  );
}

/** Class probabilities, strongest first, for the read-out. */
export function classBreakdown(
  prediction: RFPrediction | null,
): Array<{ label: RFLabel; probability: number }> {
  if (!prediction) return [];

  return Object.entries(prediction.class_probabilities)
    .map(([label, probability]) => ({ label: label as RFLabel, probability: probability ?? 0 }))
    .sort((a, b) => b.probability - a.probability);
}

/**
 * How urgently the warning lamp flashes, 0–1.
 *
 * Driven by the ETA rather than by the probability: a fault the operator has
 * four minutes to head off should not look like one that is already here.
 */
export function thermalUrgency(prediction: RFPrediction | null): number {
  if (!prediction || prediction.label !== "overheating") return 0;

  const base = prediction.severity === "alert" ? 0.6 : 0.3;
  const eta = prediction.eta_seconds;
  if (eta === null) return base;

  // 0 at two minutes out, 1 when it is happening now.
  const closeness = 1 - Math.min(eta, 120) / 120;
  return Math.min(1, base + closeness * 0.4);
}

export function topDrivers(prediction: RFPrediction | null, count = 2): RFDriver[] {
  if (!prediction) return [];
  return prediction.drivers.filter((driver) => driver.severity > 0).slice(0, count);
}

export function formatTrend(driver: RFDriver): string {
  if (Math.abs(driver.trend) < 0.001) return "steady";
  const sign = driver.trend > 0 ? "+" : "";
  return `${sign}${driver.trend.toFixed(2)} ${driver.unit}/s`;
}

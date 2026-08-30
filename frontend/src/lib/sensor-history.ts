/**
 * Types and formatting for the stored-history browser.
 *
 * The live chart plots raw readings; this plots *buckets*. A range can cover a
 * month and a million readings, so the backend aggregates before sending —
 * every point here is a min/max/average over some slice of time, and the chart
 * has to say so rather than pretending it is a sampled line.
 */

export type HistoryGranularity = "minute" | "hour" | "day" | "month" | "year";

export type HistoryBucket = {
  /** Start of the bucket, epoch milliseconds UTC. */
  t: number;
  min: number;
  max: number;
  avg: number;
  /** How many raw readings this bucket merges. */
  count: number;
  /** The same bucket before denoising. Absent when the sensor emitted no
   *  comparable raw reading for it. */
  raw_min?: number;
  raw_max?: number;
  raw_avg?: number;
};

export type HistorySummary = {
  min: number | null;
  max: number | null;
  avg: number | null;
  count: number;
  buckets: number;
};

/** Everything stored for this sensor, whatever range was asked for. */
export type HistoryExtent = {
  oldest: string | null;
  newest: string | null;
  count: number;
  min: number | null;
  max: number | null;
  avg: number | null;
};

export type SensorHistory = {
  sensor_id: string;
  unit: string;
  granularity: HistoryGranularity;
  /** True when an explicit granularity was too fine for the range and was coarsened. */
  granularity_adjusted: boolean;
  start: string | null;
  end: string | null;
  buckets: HistoryBucket[];
  summary: HistorySummary;
  raw: {
    available: boolean;
    summary: HistorySummary;
    /** True when the filter changed nothing across this range. */
    identical: boolean;
  };
  extent: HistoryExtent;
  generated_at: string;
};

export type HistoryRange = { start: Date | null; end: Date | null };

export const GRANULARITY_LABEL: Record<HistoryGranularity | "auto", string> = {
  auto: "Auto",
  minute: "Minute",
  hour: "Hour",
  day: "Day",
  month: "Month",
  year: "Year",
};

export const PRESETS: Array<{ id: string; label: string; hours: number | null }> = [
  { id: "1h", label: "1 h", hours: 1 },
  { id: "24h", label: "24 h", hours: 24 },
  { id: "7d", label: "7 d", hours: 24 * 7 },
  { id: "30d", label: "30 d", hours: 24 * 30 },
  // null means "no bounds" -- the backend then reports everything it holds,
  // which is not the same as the last N hours when the nodes have been idle.
  { id: "all", label: "All", hours: null },
];

export function presetRange(hours: number | null): HistoryRange {
  if (hours === null) return { start: null, end: null };

  const end = new Date();
  const start = new Date(end.getTime() - hours * 3600_000);
  return { start, end };
}

/**
 * Format a bucket's timestamp for the axis.
 *
 * Keyed off granularity: showing seconds on a daily bucket is noise, and
 * showing only the date on a per-minute bucket makes every tick identical.
 */
export function formatBucketTime(t: number, granularity: HistoryGranularity): string {
  const date = new Date(t);

  switch (granularity) {
    case "minute":
      return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    case "hour":
      return date.toLocaleString([], { day: "2-digit", month: "short", hour: "2-digit" });
    case "day":
      return date.toLocaleDateString([], { day: "2-digit", month: "short" });
    default:
      return date.toLocaleDateString([], { month: "short", year: "numeric" });
  }
}

/** Full timestamp for tooltips, where there is room to be unambiguous. */
export function formatBucketFull(t: number, granularity: HistoryGranularity): string {
  const date = new Date(t);

  if (granularity === "day" || granularity === "month" || granularity === "year") {
    return date.toLocaleDateString([], {
      weekday: "short",
      day: "numeric",
      month: "long",
      year: "numeric",
    });
  }
  return date.toLocaleString();
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString([], {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatCount(value: number): string {
  return value.toLocaleString();
}

/**
 * A Date as `<input type="datetime-local">` wants it: local wall-clock time,
 * no zone, minute precision. toISOString() would be UTC and shift the value
 * the user sees by their offset.
 */
export function toInputValue(date: Date | null): string {
  if (!date) return "";

  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

export function fromInputValue(value: string): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * The buckets as CSV.
 *
 * Timestamps go out as ISO UTC rather than formatted local time: this is data
 * leaving the app for a spreadsheet, and a localised string is ambiguous the
 * moment it crosses a timezone.
 */
export function toCsv(history: SensorHistory): string {
  const header = `timestamp_utc,min,avg,max,readings,unit\n`;
  const rows = history.buckets
    .map(
      (bucket) =>
        `${new Date(bucket.t).toISOString()},${bucket.min},${bucket.avg},${bucket.max},` +
        `${bucket.count},${history.unit}`,
    )
    .join("\n");

  return header + rows + "\n";
}

export function csvFilename(history: SensorHistory): string {
  const stamp = (iso: string | null, fallback: string) =>
    iso ? new Date(iso).toISOString().slice(0, 10) : fallback;

  return `skypulse-${history.sensor_id}-${stamp(history.start, "start")}-to-${stamp(history.end, "now")}-${history.granularity}.csv`;
}

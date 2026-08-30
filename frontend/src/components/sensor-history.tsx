import { useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Download, History, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DENOISERS, RAW_COLOR, RAW_DASH, denoiseCaption } from "@/lib/denoising";
import { useSensorHistory } from "@/hooks/use-sensor-history";
import { useAIInterpretation } from "@/hooks/use-ai-interpretation";
import { AINote } from "@/components/ai-note";
import type { SensorMeta } from "@/lib/sensors";
import {
  GRANULARITY_LABEL,
  PRESETS,
  csvFilename,
  formatBucketFull,
  formatBucketTime,
  formatCount,
  formatDateTime,
  fromInputValue,
  presetRange,
  toCsv,
  toInputValue,
  type HistoryBucket,
  type HistoryGranularity,
  type HistoryRange,
  type SensorHistory,
} from "@/lib/sensor-history";

/**
 * Browse what has already been stored for one sensor.
 *
 * The chart above this one is the live signal -- the last 90 seconds, redrawn
 * every second. This is the opposite: a range the user chooses, over data
 * already on disk, fetched once per choice.
 *
 * Every point is a bucket, not a reading, so the band is drawn as well as the
 * line. Plotting the average alone would quietly erase the spikes -- a minute
 * that touched 41 °C and a minute that sat flat at 24 °C can have the same
 * mean, and on a fault dashboard that difference is the whole point.
 */

const GRANULARITIES: Array<HistoryGranularity | "auto"> = ["auto", "minute", "hour", "day"];

export function SensorHistoryPanel({ sensor }: { sensor: SensorMeta }) {
  const [preset, setPreset] = useState<string>("24h");
  const [range, setRange] = useState<HistoryRange>(() => presetRange(24));
  const [granularity, setGranularity] = useState<HistoryGranularity | "auto">("auto");
  // The raw signal is opt-in by hover: it is reference material, and drawing
  // it permanently doubles the ink on a chart whose subject is the clean series.
  const [showRaw, setShowRaw] = useState(false);

  const { history, loading, error, indexing, reload } = useSensorHistory(
    sensor.id,
    range,
    granularity,
  );

  // The note answers the question the controls just asked, so it is keyed to
  // the same query: change the range or the bucket size and it is re-read.
  // No polling — a stored range does not change under you.
  const query = historyQuery(sensor.id, range, granularity);
  const note = useAIInterpretation(
    history && history.buckets.length > 0 ? query.path : null,
    query.signature,
  );

  function applyPreset(id: string, hours: number | null) {
    setPreset(id);
    setRange(presetRange(hours));
  }

  function applyBound(which: "start" | "end", value: string) {
    // Any hand-edited bound means the preset no longer describes what is shown.
    setPreset("custom");
    setRange((current) => ({ ...current, [which]: fromInputValue(value) }));
  }

  /** Jump to the newest data on record -- the fix when a range comes back empty. */
  function jumpToLatest() {
    if (!history?.extent.newest) return;

    const end = new Date(history.extent.newest);
    // A little past the last reading, so it is not sitting on the axis edge.
    end.setMinutes(end.getMinutes() + 1);

    setPreset("custom");
    setRange({ start: new Date(end.getTime() - 24 * 3600_000), end });
  }

  return (
    <div
      data-tour="sensor-history"
      className="rounded-2xl border border-border/60 bg-card p-5"
      style={{ boxShadow: "var(--shadow-elegant)" }}
    >
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium">
            <History className="h-4 w-4 text-primary" />
            Stored history
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {history?.extent.count
              ? `${formatCount(history.extent.count)} readings on record · ${formatDateTime(
                  history.extent.oldest,
                )} → ${formatDateTime(history.extent.newest)}`
              : "Recorded readings for this sensor."}
          </p>
        </div>

        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" onClick={reload} disabled={loading}>
            {loading ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
          </Button>
          <Button
            data-tour="history-export"
            variant="ghost"
            size="sm"
            onClick={() => history && downloadCsv(history)}
            disabled={!history || history.buckets.length === 0}
            title="Download the buckets as CSV"
          >
            <Download className="h-3.5 w-3.5" />
            <span className="ml-1.5 hidden sm:inline">CSV</span>
          </Button>
        </div>
      </div>

      <Controls
        preset={preset}
        range={range}
        granularity={granularity}
        onPreset={applyPreset}
        onBound={applyBound}
        onGranularity={setGranularity}
      />

      {history?.granularity_adjusted && (
        <p className="mt-2 text-[11px] text-amber-500">
          That range is too long for {GRANULARITY_LABEL[granularity].toLowerCase()} buckets —
          showing {GRANULARITY_LABEL[history.granularity].toLowerCase()} instead.
        </p>
      )}

      <div
        className="relative mt-4 h-64"
        onMouseEnter={() => setShowRaw(true)}
        onMouseLeave={() => setShowRaw(false)}
      >
        {history && history.buckets.length > 0 ? (
          <HistoryChart sensor={sensor} history={history} showRaw={showRaw} />
        ) : (
          <Placeholder
            loading={loading}
            error={error}
            indexing={indexing}
            hasRecords={Boolean(history?.extent.count)}
            onJumpToLatest={jumpToLatest}
          />
        )}

        {/* Kept as an overlay rather than replacing the chart: re-querying a
            new range should dim the old curve, not blank the panel. */}
        {loading && history && history.buckets.length > 0 && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center rounded-lg bg-background/40">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}
      </div>

      {history && history.buckets.length > 0 && (
        <>
          <RawLegend sensor={sensor} history={history} showRaw={showRaw} />
          <Summary sensor={sensor} history={history} />
          <AINote
            note={note.note}
            loading={note.loading}
            unavailable={note.unavailable}
            pending={note.pending}
            onReload={note.reload}
            label="What this range shows"
          />
        </>
      )}
    </div>
  );
}

/**
 * The key for the two curves, and what separates them.
 *
 * Doubles as the affordance: without it nothing tells the reader a second
 * series exists at all, and a hover-only feature nobody knows about is the
 * same as no feature.
 */
function RawLegend({
  sensor,
  history,
  showRaw,
}: {
  sensor: SensorMeta;
  history: SensorHistory;
  showRaw: boolean;
}) {
  const unchanged = history.raw.identical || DENOISERS[sensor.id].detail === null;

  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
      <span className="flex items-center gap-1.5">
        <span className="h-0.5 w-4 rounded-full" style={{ background: "var(--primary)" }} />
        <span className="text-muted-foreground">Denoised</span>
      </span>

      <span className="flex items-center gap-1.5">
        <span
          className="h-0 w-4 border-t-2 border-dashed"
          style={{ borderColor: RAW_COLOR, opacity: showRaw ? 1 : 0.45 }}
        />
        <span className="text-muted-foreground">
          Raw{showRaw ? "" : " · hover the chart to reveal"}
        </span>
      </span>

      <span className={`ml-auto ${unchanged ? "text-amber-500" : "text-muted-foreground"}`}>
        {denoiseCaption(sensor.id, history.raw.identical)}
      </span>
    </div>
  );
}

/**
 * The interpretation endpoint for the range currently on screen.
 *
 * Built from the same parameters as the data request so the note and the curve
 * can never describe different things, and returned with a signature the hook
 * can compare cheaply.
 */
function historyQuery(
  sensorId: string,
  range: HistoryRange,
  granularity: HistoryGranularity | "auto",
) {
  const params = new URLSearchParams({ granularity });
  if (range.start) params.set("start", range.start.toISOString());
  if (range.end) params.set("end", range.end.toISOString());

  return {
    path: `/api/ai/sensor/${encodeURIComponent(sensorId)}/history?${params}`,
    signature: `${sensorId}|${params}`,
  };
}

function Controls({
  preset,
  range,
  granularity,
  onPreset,
  onBound,
  onGranularity,
}: {
  preset: string;
  range: HistoryRange;
  granularity: HistoryGranularity | "auto";
  onPreset: (id: string, hours: number | null) => void;
  onBound: (which: "start" | "end", value: string) => void;
  onGranularity: (value: HistoryGranularity | "auto") => void;
}) {
  return (
    <div data-tour="history-controls" className="flex flex-wrap items-end gap-x-4 gap-y-3">
      <Group label="Range">
        <div className="flex rounded-lg border border-border/60 p-0.5">
          {PRESETS.map((option) => (
            <button
              key={option.id}
              type="button"
              onClick={() => onPreset(option.id, option.hours)}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                preset === option.id
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </Group>

      <Group label="From">
        <input
          type="datetime-local"
          value={toInputValue(range.start)}
          onChange={(event) => onBound("start", event.target.value)}
          className="rounded-lg border border-border/60 bg-background px-2 py-1 text-xs tabular-nums outline-none focus:border-primary"
        />
      </Group>

      <Group label="To">
        <input
          type="datetime-local"
          value={toInputValue(range.end)}
          onChange={(event) => onBound("end", event.target.value)}
          className="rounded-lg border border-border/60 bg-background px-2 py-1 text-xs tabular-nums outline-none focus:border-primary"
        />
      </Group>

      <Group label="Bucket">
        <div className="flex rounded-lg border border-border/60 p-0.5">
          {GRANULARITIES.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => onGranularity(option)}
              className={`rounded-md px-2.5 py-1 text-xs transition-colors ${
                granularity === option
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {GRANULARITY_LABEL[option]}
            </button>
          ))}
        </div>
      </Group>
    </div>
  );
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      {children}
    </div>
  );
}

function HistoryChart({
  sensor,
  history,
  showRaw,
}: {
  sensor: SensorMeta;
  history: SensorHistory;
  showRaw: boolean;
}) {
  // recharts draws a band from a [low, high] tuple, so the min/max pair is
  // reshaped into one series rather than stacking two areas.
  const data = useMemo(
    () => history.buckets.map((bucket) => ({ ...bucket, band: [bucket.min, bucket.max] })),
    [history.buckets],
  );

  const gradId = `grad-history-${sensor.id}`;

  // Fitted to the data rather than the sensor's full declared scale: a day
  // that moved 2 °C is unreadable inside a -30..60 axis.
  // Sized to hold the raw series too, even while it is hidden: recomputing the
  // axis on hover would make the clean curve jump the moment the pointer
  // enters, which reads as the data changing rather than as an overlay.
  const domain = useMemo(() => {
    const low = Math.min(...history.buckets.map((b) => Math.min(b.min, b.raw_min ?? b.min)));
    const high = Math.max(...history.buckets.map((b) => Math.max(b.max, b.raw_max ?? b.max)));
    const pad = (high - low || 1) * 0.1;
    return [low - pad, high + pad] as [number, number];
  }, [history.buckets]);

  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.28} />
            <stop offset="100%" stopColor="var(--primary)" stopOpacity={0.06} />
          </linearGradient>
        </defs>

        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
        <XAxis
          dataKey="t"
          tickFormatter={(t) => formatBucketTime(t as number, history.granularity)}
          stroke="var(--muted-foreground)"
          fontSize={11}
          tickLine={false}
          axisLine={false}
          minTickGap={48}
        />
        <YAxis
          domain={domain}
          tickFormatter={(v) => (v as number).toFixed(1)}
          stroke="var(--muted-foreground)"
          fontSize={11}
          tickLine={false}
          axisLine={false}
          width={52}
        />
        <Tooltip
          contentStyle={{
            background: "var(--popover)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            fontSize: 12,
          }}
          labelFormatter={(t) => formatBucketFull(t as number, history.granularity)}
          content={
            <HistoryTooltip
              unit={history.unit}
              granularity={history.granularity}
              showRaw={showRaw}
            />
          }
        />

        {/* Spread of readings inside each bucket. */}
        <Area
          dataKey="band"
          stroke="none"
          fill={`url(#${gradId})`}
          isAnimationActive={false}
          activeDot={false}
        />
        {/* Mean of each bucket before denoising. Drawn first so the clean
            line stays in front, and faded rather than unmounted so revealing
            it never re-lays-out the chart. */}
        <Line
          dataKey="raw_avg"
          stroke={RAW_COLOR}
          strokeWidth={1.5}
          strokeDasharray={RAW_DASH}
          strokeOpacity={showRaw ? 0.95 : 0}
          dot={false}
          activeDot={false}
          isAnimationActive={false}
          style={{ transition: "stroke-opacity 180ms ease-out" }}
        />
        {/* Mean of each bucket. */}
        <Line
          dataKey="avg"
          stroke="var(--primary)"
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function HistoryTooltip({
  active,
  payload,
  label,
  unit,
  granularity,
  showRaw,
}: {
  active?: boolean;
  payload?: Array<{ payload: HistoryBucket }>;
  label?: number;
  unit: string;
  granularity: HistoryGranularity;
  showRaw?: boolean;
}) {
  if (!active || !payload?.length) return null;

  const bucket = payload[0].payload;
  const hasRaw = showRaw && bucket.raw_avg !== undefined;
  const shift = hasRaw ? bucket.avg - (bucket.raw_avg as number) : 0;

  return (
    <div className="rounded-lg border border-border bg-popover px-2.5 py-2 text-xs shadow-md">
      <div className="font-medium">{formatBucketFull(label ?? bucket.t, granularity)}</div>
      <dl className="mt-1.5 space-y-0.5 tabular-nums">
        <Row term="Denoised" value={`${bucket.avg.toFixed(2)} ${unit}`} />
        {hasRaw && (
          <Row
            term="Raw"
            value={`${(bucket.raw_avg as number).toFixed(2)} ${unit}`}
            color={RAW_COLOR}
          />
        )}
        {hasRaw && (
          <Row term="Filter shift" value={`${shift >= 0 ? "+" : ""}${shift.toFixed(3)} ${unit}`} />
        )}
        <Row term="Min" value={`${bucket.min.toFixed(2)} ${unit}`} />
        <Row term="Max" value={`${bucket.max.toFixed(2)} ${unit}`} />
        <Row term="Readings" value={formatCount(bucket.count)} />
      </dl>
    </div>
  );
}

function Row({ term, value, color }: { term: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-muted-foreground">{term}</dt>
      <dd style={color ? { color } : undefined}>{value}</dd>
    </div>
  );
}

function Summary({ sensor, history }: { sensor: SensorMeta; history: SensorHistory }) {
  const { summary } = history;

  return (
    <div className="mt-4 grid grid-cols-2 gap-3 border-t border-border/50 pt-3 sm:grid-cols-4">
      <Figure label="Min" value={summary.min} unit={sensor.unit} />
      <Figure label="Average" value={summary.avg} unit={sensor.unit} />
      <Figure label="Max" value={summary.max} unit={sensor.unit} />
      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Readings</div>
        <div className="mt-0.5 text-sm font-semibold tabular-nums">
          {formatCount(summary.count)}
        </div>
        <div className="text-[10px] text-muted-foreground">
          in {formatCount(summary.buckets)} {history.granularity} buckets
        </div>
      </div>
    </div>
  );
}

function Figure({ label, value, unit }: { label: string; value: number | null; unit: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-sm font-semibold tabular-nums">
        {value === null ? "—" : `${value.toFixed(2)} ${unit}`}
      </div>
    </div>
  );
}

function Placeholder({
  loading,
  error,
  indexing,
  hasRecords,
  onJumpToLatest,
}: {
  loading: boolean;
  error: string | null;
  indexing: boolean;
  hasRecords: boolean;
  onJumpToLatest: () => void;
}) {
  return (
    <div className="grid h-full place-items-center rounded-lg border border-dashed border-border/60 px-6 text-center">
      <div className="max-w-sm">
        {loading ? (
          <p className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading history…
          </p>
        ) : indexing ? (
          <>
            <p className="text-sm text-muted-foreground">Building the history index…</p>
            <p className="mt-1 text-xs text-muted-foreground">
              This happens once, and takes a few minutes over a large database.
            </p>
          </>
        ) : error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : (
          <>
            <p className="text-sm text-muted-foreground">No readings in this range.</p>
            {/* The nodes run in bursts, so an empty window is far more often
                the wrong dates than missing data -- point at the fix. */}
            {hasRecords && (
              <Button variant="outline" size="sm" className="mt-3" onClick={onJumpToLatest}>
                Jump to the latest data
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function downloadCsv(history: SensorHistory) {
  const blob = new Blob([toCsv(history)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);

  const link = document.createElement("a");
  link.href = url;
  link.download = csvFilename(history);
  link.click();

  // Revoked on the next tick: revoking synchronously can beat the download
  // starting in some browsers.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

import { useCallback, useEffect, useRef, useState } from "react";
import { apiUrl } from "@/lib/api";
import type { HistoryGranularity, HistoryRange, SensorHistory } from "@/lib/sensor-history";

type State = {
  history: SensorHistory | null;
  loading: boolean;
  error: string | null;
  /** True while CouchDB is still building the history index (a one-time wait). */
  indexing: boolean;
  reload: () => void;
};

/**
 * Fetch bucketed history for one sensor.
 *
 * Deliberately not polled. This is a query the user asked for over data that
 * has already been written; re-running it every second would fight the range
 * they picked and hammer a view that scans months. The live chart above it is
 * where "now" lives.
 */
export function useSensorHistory(
  sensorId: string,
  range: HistoryRange,
  granularity: HistoryGranularity | "auto",
): State {
  const [history, setHistory] = useState<SensorHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [indexing, setIndexing] = useState(false);
  const [nonce, setNonce] = useState(0);

  // Dates are fresh objects on every render, so the effect keys off their
  // numeric value instead -- otherwise picking no new range still refetches.
  const startMs = range.start?.getTime() ?? null;
  const endMs = range.end?.getTime() ?? null;

  const abortRef = useRef<AbortController | null>(null);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    // Cancel whatever the previous range asked for: clicking through presets
    // otherwise leaves responses racing, and the slowest one wins the chart.
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const params = new URLSearchParams({ granularity });
    if (startMs !== null) params.set("start", new Date(startMs).toISOString());
    if (endMs !== null) params.set("end", new Date(endMs).toISOString());

    setLoading(true);

    (async () => {
      try {
        const response = await fetch(
          apiUrl(`/api/telemetry/${encodeURIComponent(sensorId)}/history?${params}`),
          { signal: controller.signal },
        );

        const body = await response.json();

        if (!response.ok) {
          // 503 is the index still building -- worth its own message, because
          // it resolves on its own and "no data" would be the wrong conclusion.
          setIndexing(response.status === 503);
          throw new Error(body?.detail ?? `History request failed (${response.status})`);
        }

        setHistory(body as SensorHistory);
        setError(null);
        setIndexing(false);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Failed to load history");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();

    return () => controller.abort();
  }, [sensorId, startMs, endMs, granularity, nonce]);

  return { history, loading, error, indexing, reload };
}

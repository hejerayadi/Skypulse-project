import { useEffect, useRef, useState } from "react";
import { apiUrl } from "@/lib/api";
import type { RFPrediction } from "@/lib/rf-fault";

type RFFaultState = {
  prediction: RFPrediction | null;
  /** True while the SSE connection is open. False means we are on the fallback poll. */
  connected: boolean;
  error: string | null;
};

/**
 * Subscribe to the backend's RF fault stream.
 *
 * Server-sent events rather than polling: a prediction arrives per reading, and
 * the whole point of the 3D warning is that it appears when the fault does
 * rather than up to a poll interval later. EventSource reconnects on its own
 * using the `retry` the backend sends, so there is no backoff logic here.
 *
 * The poll is a fallback, not a partner — it only runs while the stream is
 * down, so the two never race to set state.
 */
export function useRFFault(fallbackIntervalMs = 4000): RFFaultState {
  const [prediction, setPrediction] = useState<RFPrediction | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Read by the fallback poll, which must not overwrite a fresher SSE frame.
  const connectedRef = useRef(false);

  useEffect(() => {
    // EventSource is browser-only; this effect never runs during SSR, but a
    // non-browser runtime rendering the tree would still hit the guard.
    if (typeof window === "undefined" || typeof EventSource === "undefined") return;

    const source = new EventSource(apiUrl("/api/rf/stream"));

    source.onopen = () => {
      connectedRef.current = true;
      setConnected(true);
      setError(null);
    };

    source.onmessage = (event) => {
      try {
        setPrediction(JSON.parse(event.data) as RFPrediction);
      } catch {
        // A malformed frame is not worth tearing the connection down for.
      }
    };

    source.onerror = () => {
      connectedRef.current = false;
      setConnected(false);
      // Keep the last prediction on screen: a dropped connection is not the
      // same as the fault clearing, and blanking the alert would be a lie.
      setError("Live stream interrupted — retrying.");
    };

    return () => {
      connectedRef.current = false;
      source.close();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      if (connectedRef.current) return;

      try {
        const response = await fetch(apiUrl("/api/rf/status"));
        if (!response.ok) throw new Error(`RF status failed (${response.status})`);

        const data = (await response.json()) as {
          latest: RFPrediction | null;
          detector_error?: string;
        };
        if (cancelled || connectedRef.current) return;

        if (data.latest) setPrediction(data.latest);
        setError(data.detector_error ?? null);
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "RF detector unreachable");
      }
    }

    const id = window.setInterval(poll, fallbackIntervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [fallbackIntervalMs]);

  return { prediction, connected, error };
}

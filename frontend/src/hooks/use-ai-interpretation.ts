import { useCallback, useEffect, useRef, useState } from "react";
import { apiUrl } from "@/lib/api";

export type AIInterpretation = {
  interpretation: string;
  signature: string;
  /** Served from the backend's cache rather than a fresh completion. */
  cached: boolean;
  /** The model could not be reached; this is the last note it managed. */
  stale: boolean;
  age_seconds: number;
  model: string;
  generated_at: string;
};

type State = {
  note: AIInterpretation | null;
  loading: boolean;
  /** Set when interpretations are switched off or the model is unreachable. */
  unavailable: string | null;
  /** Set when there is simply nothing to interpret yet. */
  pending: string | null;
  reload: () => void;
};

/**
 * Fetch one AI note.
 *
 * Nothing here is on a timer. A note is read once when the panel has something
 * to say, again whenever `situation` changes — a new fault state, a new date
 * range — and otherwise only when the reader presses refresh.
 *
 * `reload()` sends `force=true`, which skips the backend's cache. Without that
 * the button would hand back the sentence already on screen and look broken.
 */
export function useAIInterpretation(path: string | null, situation: string): State {
  const [note, setNote] = useState<AIInterpretation | null>(null);
  const [loading, setLoading] = useState(false);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  // Distinguishes "the situation changed, read it" from "the reader asked
  // again" — only the second is allowed to bypass the cache.
  const forceRef = useRef(false);

  const reload = useCallback(() => {
    forceRef.current = true;
    setNonce((n) => n + 1);
  }, []);

  useEffect(() => {
    if (!path) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const forced = forceRef.current;
    forceRef.current = false;

    async function run() {
      setLoading(true);
      try {
        const url = forced ? `${path}${path!.includes("?") ? "&" : "?"}force=true` : path!;
        const response = await fetch(apiUrl(url), { signal: controller.signal });
        const body = await response.json();

        if (controller.signal.aborted) return;

        if (response.status === 503) {
          // Not configured, or the model is down. Distinct from an error: the
          // rest of the page is fine and the panel just says so quietly.
          setUnavailable(body?.detail ?? "AI interpretation unavailable");
          return;
        }
        if (response.status === 409 || response.status === 404) {
          // Nothing to interpret yet — an empty range, or a model window still
          // filling. Worth saying, and it resolves on its own.
          setPending(body?.detail ?? "Nothing to interpret yet");
          setNote(null);
          return;
        }
        if (!response.ok) {
          setUnavailable(body?.detail ?? `Interpretation failed (${response.status})`);
          return;
        }

        setNote(body as AIInterpretation);
        setUnavailable(null);
        setPending(null);
      } catch (err) {
        if (controller.signal.aborted) return;
        setUnavailable(err instanceof Error ? err.message : "Interpretation failed");
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }

    run();

    return () => controller.abort();
  }, [path, situation, nonce]);

  return { note, loading, unavailable, pending, reload };
}

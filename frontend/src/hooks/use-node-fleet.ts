import { useEffect, useState } from "react";
import { apiUrl } from "@/lib/api";
import { EMPTY_FLEET, type NodeFleet } from "@/lib/nodes";

export function useNodeFleet(intervalMs = 5000) {
  const [fleet, setFleet] = useState<NodeFleet>(EMPTY_FLEET);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadFleet() {
      try {
        const response = await fetch(apiUrl("/api/nodes"));
        if (!response.ok) {
          throw new Error(`Node request failed (${response.status})`);
        }

        const data = (await response.json()) as NodeFleet;
        if (cancelled) return;

        setFleet(data);
        setError(null);
      } catch (err) {
        if (cancelled) return;

        // Keep the last known fleet on screen: a dropped poll is not the same
        // as the nodes disappearing.
        setError(err instanceof Error ? err.message : "Failed to load nodes");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadFleet();
    const id = window.setInterval(loadFleet, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [intervalMs]);

  return { fleet, loading, error };
}

import { useEffect, useState } from "react";
import type { SensorMeta } from "@/lib/sensors";

export type SensorPoint = { t: number; v: number };

type TelemetrySeriesResponse = {
  sensor_id: string;
  device_id: string;
  unit: string;
  latest: number;
  delta: number;
  points: SensorPoint[];
  generated_at: string;
};

function buildTelemetryUrl(sensorId: string, capacity: number, intervalMs: number) {
  // Relative /api URLs work in production, while the Vite proxy handles local development.
  const apiBase = import.meta.env.VITE_TELEMETRY_API_BASE_URL ?? "";
  const normalizedBase = apiBase.endsWith("/") ? apiBase.slice(0, -1) : apiBase;
  return `${normalizedBase}/api/telemetry/${encodeURIComponent(sensorId)}?samples=${capacity}&interval_ms=${intervalMs}`;
}

export function useSensorStream(sensor: SensorMeta, capacity = 60, intervalMs = 1000) {
  const [points, setPoints] = useState<SensorPoint[]>([]);
  const [latest, setLatest] = useState(sensor.base);
  const [delta, setDelta] = useState(0);

  useEffect(() => {
    let cancelled = false;

    async function loadTelemetry() {
      // We fetch the whole series each time so the UI stays stateless and easy to swap later.
      try {
        const response = await fetch(buildTelemetryUrl(sensor.id, capacity, intervalMs));
        if (!response.ok) {
          throw new Error(`Telemetry request failed (${response.status})`);
        }

        const data = (await response.json()) as TelemetrySeriesResponse;
        if (cancelled) return;

        setPoints(data.points ?? []);
        setLatest(typeof data.latest === "number" ? data.latest : sensor.base);
        setDelta(typeof data.delta === "number" ? data.delta : 0);
      } catch (error) {
        if (cancelled) return;

        // If the backend is unavailable, keep the chart from crashing and leave the series empty.
        console.error("Failed to load telemetry", error);
        setPoints([]);
        setLatest(sensor.base);
        setDelta(0);
      }
    }

    loadTelemetry();
    const id = window.setInterval(loadTelemetry, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [capacity, intervalMs, sensor.base, sensor.id]);

  return { points, latest, delta };
}
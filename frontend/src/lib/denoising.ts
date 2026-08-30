/**
 * Shared bits for the "what did the filter remove?" overlay.
 *
 * Both charts on the sensor page draw the denoised signal by default and lay
 * the raw one over it on hover. Keeping the colour, the dash and the wording in
 * one place means the two cannot drift into looking like different features.
 */

import type { SensorId } from "@/lib/sensors";

/** Amber against the primary blue, and dashed — so the two curves stay
 *  distinguishable without relying on colour alone. */
export const RAW_COLOR = "#f59e0b";
export const RAW_DASH = "4 3";

export type Denoiser = {
  /** What actually runs, named so the chart can say more than "filtered". */
  name: string;
  /** Null when nothing filters this sensor. */
  detail: string | null;
};

/**
 * The filter behind each sensor's `filtered_value`, from
 * backend/app/denoising/.
 */
export const DENOISERS: Record<SensorId, Denoiser> = {
  temperature: { name: "Median filter", detail: "5-sample sliding median" },
  humidity: { name: "None", detail: null },
  pressure: { name: "Kalman filter", detail: "process noise 0.001, measurement noise 0.0625" },
  acoustic: { name: "Butterworth low-pass", detail: "0.18 Hz cutoff, zero-phase" },
  motion: {
    name: "Butterworth low-pass",
    detail: "0.15 Hz cutoff per axis, magnitude recomputed",
  },
};

/**
 * What to tell the reader underneath the chart.
 *
 * `identical` comes from the backend comparing the two series over the range
 * actually shown, so this reports what happened to *this* data rather than
 * what the config implies should happen. The two disagree for temperature —
 * see the note in DENOISERS — and the honest thing is to say the curves match
 * rather than to imply a filter did something it did not.
 */
export function denoiseCaption(sensorId: SensorId, identical: boolean): string {
  const denoiser = DENOISERS[sensorId];

  if (denoiser.detail === null) {
    return `No denoiser for this sensor yet — the stored value is the raw reading.`;
  }
  if (identical) {
    return `${denoiser.name} (${denoiser.detail}) — but it left this range unchanged.`;
  }
  return `${denoiser.name} · ${denoiser.detail}`;
}

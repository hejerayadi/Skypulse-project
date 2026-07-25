import { Thermometer, Droplets, Gauge, Volume2, Waves, type LucideIcon } from "lucide-react";

export type SensorId = "temperature" | "humidity" | "pressure" | "acoustic" | "motion";

export type SensorMeta = {
  id: SensorId;
  name: string;
  unit: string;
  icon: LucideIcon;
  min: number;
  max: number;
  base: number;
  noise: number;
  description: string;
};

export const SENSORS: SensorMeta[] = [
  { id: "temperature", name: "Temperature", unit: "°C", icon: Thermometer, min: -30, max: 60, base: 22, noise: 0.8, description: "Onboard thermal reading from the payload bay." },
  { id: "humidity", name: "Humidity", unit: "%", icon: Droplets, min: 0, max: 100, base: 45, noise: 1.5, description: "Relative humidity inside the pressurized compartment." },
  { id: "pressure", name: "Pressure", unit: "hPa", icon: Gauge, min: 900, max: 1080, base: 1013, noise: 1.2, description: "Barometric pressure at the ground-station reference." },
  { id: "acoustic", name: "Acoustic", unit: "dB", icon: Volume2, min: 20, max: 130, base: 55, noise: 3, description: "Ambient acoustic level captured by onboard microphone." },
  { id: "motion", name: "Motion / Vibration", unit: "m/s²", icon: Waves, min: 0, max: 20, base: 0.3, noise: 0.4, description: "Structural vibration and acceleration on the primary axis." },
];

export function getSensor(id: string): SensorMeta | undefined {
  return SENSORS.find((s) => s.id === id);
}

export function sampleValue(sensor: SensorMeta, t: number): number {
  const slow = Math.sin(t / 12000 + sensor.base) * (sensor.noise * 3);
  const fast = (Math.random() - 0.5) * sensor.noise * 2;
  const raw = sensor.base + slow + fast;
  return Math.max(sensor.min, Math.min(sensor.max, raw));
}
// Relative /api URLs work in production, while the Vite proxy handles local development.
const API_BASE = (import.meta.env.VITE_TELEMETRY_API_BASE_URL ?? "").replace(/\/$/, "");

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

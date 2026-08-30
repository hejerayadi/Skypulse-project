import { createFileRoute, Link } from "@tanstack/react-router";
import { SENSORS } from "@/lib/sensors";
import { SensorMiniCard } from "@/components/sensor-mini-card";

export const Route = createFileRoute("/_authenticated/dashboard/")({
  component: Overview,
});

function Overview() {
  return (
    <div className="mx-auto max-w-6xl">
      <div className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Mission overview</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Live telemetry across the payload. Click a card for detail and AI interpretation.
        </p>
      </div>

      <div data-tour="overview-grid" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {SENSORS.map((sensor) => (
          <Link
            key={sensor.id}
            to="/dashboard/$sensor"
            params={{ sensor: sensor.id }}
            className="group focus:outline-none"
          >
            <SensorMiniCard sensor={sensor} />
          </Link>
        ))}
      </div>
    </div>
  );
}

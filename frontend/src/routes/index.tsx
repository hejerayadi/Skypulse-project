import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { fetchCurrentUser } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Satellite, Radio, Activity, Sparkles } from "lucide-react";

export const Route = createFileRoute("/")({
  component: Landing,
});

function Landing() {
  const [authed, setAuthed] = useState(false);
  useEffect(() => {
    // Only decides which label the buttons get — the real gate is the backend
    // check in _authenticated/route.tsx.
    fetchCurrentUser().then((user) => setAuthed(user !== null));
  }, []);

  return (
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          background:
            "radial-gradient(600px circle at 20% 10%, var(--primary), transparent 60%), radial-gradient(500px circle at 80% 90%, var(--primary-glow), transparent 60%)",
        }}
      />
      <header className="relative z-10 mx-auto flex max-w-6xl items-center justify-between px-6 py-6">
        <div className="flex items-center gap-2">
          <div className="grid h-9 w-9 place-items-center rounded-lg" style={{ background: "var(--gradient-primary)" }}>
            <Satellite className="h-5 w-5 text-primary-foreground" />
          </div>
          <span className="text-lg font-semibold tracking-tight">SkyPulse</span>
        </div>
        <Button asChild variant={authed ? "default" : "outline"}>
          <Link to={authed ? "/dashboard" : "/auth"}>{authed ? "Open console" : "Sign in"}</Link>
        </Button>
      </header>

      <main className="relative z-10 mx-auto max-w-6xl px-6 pb-24 pt-16 md:pt-28">
        <div className="max-w-3xl">
          <div className="mb-6 inline-flex items-center gap-2 rounded-full border border-border/60 bg-card/50 px-3 py-1 text-xs text-muted-foreground backdrop-blur">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-primary" />
            Live ground-station telemetry
          </div>
          <h1 className="text-4xl font-semibold leading-tight tracking-tight md:text-6xl">
            Mission control for your{" "}
            <span
              className="bg-clip-text text-transparent"
              style={{ backgroundImage: "var(--gradient-primary)" }}
            >
              CubeSat
            </span>{" "}
            &amp; satellite payloads.
          </h1>
          <p className="mt-6 max-w-2xl text-lg text-muted-foreground">
            SkyPulse streams temperature, humidity, pressure, acoustic and motion telemetry
            in real time — with an AI copilot that turns raw signals into mission insight.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button asChild size="lg">
              <Link to={authed ? "/dashboard" : "/auth"}>
                {authed ? "Enter console" : "Get started"}
              </Link>
            </Button>
            <Button asChild size="lg" variant="outline">
              <Link to="/auth" search={{ mode: "signup" } as never}>
                Create account
              </Link>
            </Button>
          </div>
        </div>

        <div className="mt-20 grid gap-4 md:grid-cols-3">
          {[
            { icon: Radio, title: "Real-time streams", desc: "Sub-second telemetry from every onboard sensor." },
            { icon: Activity, title: "Signal analytics", desc: "Trend, anomaly and threshold detection built-in." },
            { icon: Sparkles, title: "AI interpretation", desc: "Plain-language insights on every variation." },
          ].map(({ icon: Icon, title, desc }) => (
            <div
              key={title}
              className="rounded-2xl border border-border/60 bg-card/60 p-6 backdrop-blur"
              style={{ boxShadow: "var(--shadow-elegant)" }}
            >
              <Icon className="h-6 w-6 text-primary" />
              <h3 className="mt-4 font-semibold">{title}</h3>
              <p className="mt-1 text-sm text-muted-foreground">{desc}</p>
            </div>
          ))}
        </div>
      </main>
    </div>
  );
}

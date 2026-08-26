import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import { Satellite, Loader2 } from "lucide-react";

import { fetchCurrentUser, login, register } from "@/lib/auth";

export const Route = createFileRoute("/auth")({
  head: () => ({
    meta: [
      { title: "Sign in — SkyPulse" },
      { name: "description", content: "Access your SkyPulse ground-station console." },
    ],
  }),
  component: AuthPage,
});

function AuthPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [organization, setOrganization] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    // Already signed in? The backend still has the cookie, so skip the form.
    fetchCurrentUser().then((user) => {
      if (user) navigate({ to: "/dashboard" });
    });
  }, [navigate]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      if (mode === "signup") {
        // The backend creates the account in Supabase, assigns the role and
        // opens the session; this only reports what it said.
        const session = await register({
          email,
          password,
          full_name: fullName.trim() || undefined,
          organization: organization.trim() || undefined,
        });

        // With "Confirm email" enabled in Supabase there is no session yet —
        // sending them to the dashboard would just bounce them back here.
        if (session.email_confirmation_required) {
          toast.success(`Account created. Check ${session.user.email} for the confirmation link.`);
          setMode("signin");
          setPassword("");
          return;
        }

        toast.success(`Account created. Welcome to SkyPulse, ${session.user.email}.`);
      } else {
        await login({ email, password });
        toast.success("Signed in.");
      }
      navigate({ to: "/dashboard" });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Authentication failed";
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background px-4">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-40"
        style={{
          background:
            "radial-gradient(600px circle at 30% 20%, var(--primary), transparent 60%), radial-gradient(500px circle at 70% 80%, var(--primary-glow), transparent 60%)",
        }}
      />
      <div
        className="relative z-10 w-full max-w-md rounded-2xl border border-border/60 bg-card/80 p-8 backdrop-blur"
        style={{ boxShadow: "var(--shadow-elegant)" }}
      >
        <div className="mb-6 flex items-center gap-2">
          <div
            className="grid h-10 w-10 place-items-center rounded-lg"
            style={{ background: "var(--gradient-primary)" }}
          >
            <Satellite className="h-5 w-5 text-primary-foreground" />
          </div>
          <div>
            <div className="text-lg font-semibold tracking-tight">SkyPulse</div>
            <div className="text-xs text-muted-foreground">Ground station console</div>
          </div>
        </div>

        <h1 className="text-2xl font-semibold tracking-tight">
          {mode === "signin" ? "Welcome back" : "Create your account"}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {mode === "signin"
            ? "Sign in to monitor your mission telemetry."
            : "Start streaming telemetry in seconds."}
        </p>

        <form onSubmit={submit} className="mt-6 space-y-4">
          {mode === "signup" && (
            <>
              <div className="space-y-2">
                <Label htmlFor="full-name">Full name</Label>
                <Input
                  id="full-name"
                  autoComplete="name"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  placeholder="Amin Bali"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="organization">Organization</Label>
                <Input
                  id="organization"
                  autoComplete="organization"
                  value={organization}
                  onChange={(e) => setOrganization(e.target.value)}
                  placeholder="IEEE AESS"
                />
              </div>
            </>
          )}
          <div className="space-y-2">
            <Label htmlFor="email">Email</Label>
            <Input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="operator@mission.dev"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              required
              autoComplete={mode === "signin" ? "current-password" : "new-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
            {mode === "signup" && (
              // A hint, not a check — the rule that counts is enforced in
              // backend/app/authentification/models.py.
              <p className="text-xs text-muted-foreground">
                At least 8 characters, with one letter and one digit.
              </p>
            )}
          </div>
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : mode === "signin" ? "Sign in" : "Create account"}
          </Button>
        </form>

        <div className="mt-6 text-center text-sm text-muted-foreground">
          {mode === "signin" ? (
            <>
              New to SkyPulse?{" "}
              <button
                type="button"
                onClick={() => setMode("signup")}
                className="font-medium text-primary hover:underline"
              >
                Create an account
              </button>
            </>
          ) : (
            <>
              Already registered?{" "}
              <button
                type="button"
                onClick={() => setMode("signin")}
                className="font-medium text-primary hover:underline"
              >
                Sign in
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

import { Loader2, RefreshCw, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { AIInterpretation } from "@/hooks/use-ai-interpretation";

/**
 * One AI-written note about whatever chart it sits under.
 *
 * Presentation only — the three places this appears differ in what they ask
 * about, not in how the answer looks, and keeping that in one component is what
 * stops them drifting into three slightly different treatments.
 *
 * It is deliberately quiet: smaller than the chart, no card of its own by
 * default, and it says nothing at all rather than showing an error when the
 * model is unreachable. The chart is the source of truth; this is a reading of
 * it.
 */
export function AINote({
  note,
  loading,
  unavailable,
  pending,
  onReload,
  label = "AI reading",
  accent,
}: {
  note: AIInterpretation | null;
  loading: boolean;
  unavailable: string | null;
  pending: string | null;
  onReload?: () => void;
  label?: string;
  /** Colour for the icon, so the note picks up the state it describes. */
  accent?: string;
}) {
  // Nothing to say and nothing pending: render nothing rather than an empty box.
  if (!note && !loading && !pending && !unavailable) return null;

  return (
    <div className="mt-3 rounded-xl border border-border/50 bg-muted/30 px-3 py-2.5">
      <div className="mb-1 flex items-center gap-1.5">
        <Sparkles className="h-3 w-3" style={{ color: accent ?? "var(--primary)" }} />
        <span className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </span>

        {note?.stale && (
          <span className="text-[10px] text-amber-500" title="The model could not be reached">
            · last known
          </span>
        )}

        {/* Notes are not on a timer, so this is the only way to get a new one
            for an unchanged situation — it needs a label, not just an icon. */}
        {onReload && (
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-6 gap-1.5 px-2 text-[10px]"
            onClick={onReload}
            disabled={loading}
            title="Ask the model again"
          >
            {loading ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="h-3 w-3" />
            )}
            {loading ? "Reading" : "Refresh"}
          </Button>
        )}
      </div>

      {/* The previous note stays visible while a new one is fetched — swapping
          it for a spinner makes the panel flicker every time the situation
          changes, which on a live chart is often. */}
      <p className="text-xs leading-relaxed text-foreground/85">
        {note?.interpretation ?? (loading ? "Reading the data…" : (pending ?? unavailable ?? ""))}
      </p>
    </div>
  );
}

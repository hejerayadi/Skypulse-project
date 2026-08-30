import { useEffect, useRef, useState } from "react";
import { useNavigate, useRouterState } from "@tanstack/react-router";
import { MessageCircle, X, Send, Loader2, Sparkles, MapPin, Navigation } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiUrl } from "@/lib/api";
import {
  dismissSpotlight,
  spotlight,
  subscribeSpotlightArea,
  type SpotlightArea,
} from "@/lib/spotlight";

/**
 * The in-app assistant.
 *
 * It answers in two channels at once. The prose lands in the chat; the
 * `target` — when the question was about something with a place on screen —
 * navigates there, scrolls to it, and hands off to SpotlightOverlay, which
 * walks a pointer onto it and dims everything else.
 *
 * The route is never chosen here. The backend resolves the model's answer
 * against a fixed table of destinations (backend/app/ai/platform_map.py), so
 * this component only ever navigates to a route that already exists.
 */

type Target = {
  id: string;
  label: string;
  route: string;
  params: Record<string, string>;
  anchor: string;
  /** Typed out on the page beside the element once the reader arrives. */
  guide?: string;
};

type Msg = {
  role: "user" | "assistant";
  content: string;
  target?: Target | null;
  /** Set when the target's element never appeared, so the chip can say so. */
  missed?: boolean;
};

const GREETING: Msg = {
  role: "assistant",
  content:
    'Hi — I\'m the SkyPulse copilot. Ask me about anything on the dashboard and I\'ll explain it and take you to it. Try "where do I see last week\'s pressure?" or "is anything wrong right now?".',
};

const SUGGESTIONS = [
  "What is the raw overlay?",
  "Is anything wrong right now?",
  "Where do I filter history by date?",
];

/** How faded the panel goes when the pointer is elsewhere. */
const IDLE_OPACITY = 0.28;

/**
 * How long a new reply stays fully visible even with the pointer away.
 *
 * The copilot's whole job is to explain something and then send you to look at
 * it — which means the pointer is very often nowhere near the panel exactly
 * when its most useful sentence arrives. Fading that out immediately would
 * hide the answer at the moment it matters.
 */
const READ_GRACE_MS = 7000;

/** The panel's own footprint, for working out whether it is in the way. */
const PANEL_WIDTH = 384;
const PANEL_HEIGHT = 560;
/** Matches the bottom-6 right-6 anchor. */
const PANEL_INSET = 24;

type Offset = { x: number; y: number };

const HOME: Offset = { x: 0, y: 0 };

/** Keep a dragged panel wholly on screen, however the window is resized. */
function clampOffset(offset: Offset): Offset {
  if (typeof window === "undefined") return offset;

  const width = Math.min(PANEL_WIDTH, window.innerWidth * 0.92);
  // Offsets run from the bottom-right anchor, so they are zero or negative.
  const minX = Math.min(0, -(window.innerWidth - width - PANEL_INSET * 2));
  const minY = Math.min(0, -(window.innerHeight - PANEL_HEIGHT - PANEL_INSET * 2));

  return {
    x: Math.max(minX, Math.min(0, offset.x)),
    y: Math.max(minY, Math.min(0, offset.y)),
  };
}

/**
 * Where the panel should actually sit.
 *
 * Two things have an opinion. The reader may have dragged it somewhere, which
 * is a deliberate choice and wins by default. But the copilot's job is to point
 * at things, and a panel parked on top of the thing it just pointed at defeats
 * that — so while a highlight is up, a handful of positions are scored by how
 * much of it they would cover and the least-bad one is used.
 *
 * The reader's own position is scored first and kept on ties, so the panel only
 * leaves where they put it when staying would genuinely be in the way, and it
 * goes straight back the moment the highlight is dismissed.
 */
function panelOffset(area: SpotlightArea | null, parked: Offset): Offset {
  if (!area || typeof window === "undefined") return parked;

  const vw = window.innerWidth;
  const vh = window.innerHeight;

  const shiftX = -(vw - PANEL_WIDTH - PANEL_INSET * 2);
  const shiftY = -(vh - PANEL_HEIGHT - PANEL_INSET * 2);

  const candidates: Offset[] = [parked];
  if (shiftX < -40) {
    candidates.push({ x: shiftX, y: parked.y }, { x: shiftX, y: 0 });
  }
  if (shiftY < -40) {
    candidates.push({ x: parked.x, y: shiftY });
    if (shiftX < -40) candidates.push({ x: shiftX, y: shiftY });
  }
  candidates.push(HOME);

  const overlapAt = (offset: Offset) => {
    const left = vw - PANEL_INSET - PANEL_WIDTH + offset.x;
    const top = vh - PANEL_INSET - PANEL_HEIGHT + offset.y;

    const wide = Math.min(area.right, left + PANEL_WIDTH) - Math.max(area.left, left);
    const tall = Math.min(area.bottom, top + PANEL_HEIGHT) - Math.max(area.top, top);
    return Math.max(0, wide) * Math.max(0, tall);
  };

  let best = candidates[0];
  let bestOverlap = overlapAt(best);

  for (const candidate of candidates.slice(1)) {
    const overlap = overlapAt(candidate);
    // A clear margin, so a few stray pixels never make the panel jump.
    if (overlap < bestOverlap - 400) {
      best = candidate;
      bestOverlap = overlap;
    }
  }

  return clampOffset(best);
}

export function CopilotBubble() {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([GREETING]);

  // The panel goes translucent when the pointer is elsewhere, so it stops
  // covering the dashboard it is describing. Focus counts as presence — fading
  // out mid-sentence while someone is typing would be absurd — and so does a
  // freshly arrived reply, for READ_GRACE_MS.
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [reading, setReading] = useState(false);
  // Named while a jump is in flight. Getting to a target involves waiting for
  // the page to mount and settle before scrolling, which is long enough that
  // saying nothing reads as the assistant having stalled.
  const [travellingTo, setTravellingTo] = useState<string | null>(null);
  const [spotArea, setSpotArea] = useState<SpotlightArea | null>(null);

  // Where the reader has parked the panel, as an offset from its bottom-right
  // home. Kept in this component, which never unmounts, so it survives closing
  // the chat and moving between pages.
  const [parked, setParked] = useState<Offset>(HOME);
  const [dragging, setDragging] = useState(false);

  const panelRef = useRef<HTMLDivElement>(null);
  // The whole drag lives in a ref. Putting it in state meant a setState per
  // pointermove, which re-rendered the entire panel — message list and all —
  // sixty times a second, and that is what made dragging feel broken. Now the
  // transform is written straight to the node while moving and committed to
  // state once, on release.
  const grab = useRef<{
    pointerId: number;
    pointerX: number;
    pointerY: number;
    from: Offset;
    latest: Offset;
  } | null>(null);

  useEffect(() => subscribeSpotlightArea(setSpotArea), []);

  // A window narrow enough to strand the panel off-screen would otherwise
  // leave it unreachable, with no way to drag it back.
  useEffect(() => {
    const onResize = () => setParked((current) => clampOffset(current));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const navigate = useNavigate();
  const currentRoute = useRouterState({ select: (s) => s.location.pathname });
  const scrollRef = useRef<HTMLDivElement>(null);
  const graceRef = useRef<number | undefined>(undefined);

  const dimmed = !hovered && !focused && !reading;
  // While a drag is in progress the reader is the only authority on where this
  // goes; the highlight gets its say again on release.
  const offset = dragging
    ? (grab.current?.latest ?? parked)
    : panelOffset(open ? spotArea : null, parked);

  function startDrag(event: React.PointerEvent<HTMLElement>) {
    // The header carries the close button; a drag must not swallow its click.
    if ((event.target as HTMLElement).closest("button")) return;
    // Ignore right-clicks and extra fingers.
    if (event.button !== 0 || grab.current) return;

    event.preventDefault();
    grab.current = {
      pointerId: event.pointerId,
      pointerX: event.clientX,
      pointerY: event.clientY,
      from: parked,
      latest: parked,
    };
    setDragging(true);
  }

  // Listeners on the window rather than pointer capture on the header. Capture
  // is retargeted through React's synthetic event system and gets lost when the
  // captured node re-renders; the window always sees the whole gesture,
  // including the part where the pointer leaves the panel or the browser.
  useEffect(() => {
    if (!dragging) return;

    const move = (event: PointerEvent) => {
      const start = grab.current;
      if (!start || event.pointerId !== start.pointerId) return;

      const next = clampOffset({
        x: start.from.x + (event.clientX - start.pointerX),
        y: start.from.y + (event.clientY - start.pointerY),
      });

      start.latest = next;
      // Written straight to the node: no render, no transition, no lag.
      if (panelRef.current) {
        panelRef.current.style.transform = `translate3d(${next.x}px, ${next.y}px, 0)`;
      }
    };

    const finish = (event: PointerEvent) => {
      const start = grab.current;
      if (!start || event.pointerId !== start.pointerId) return;

      grab.current = null;
      setParked(start.latest);
      setDragging(false);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
    // Losing the pointer to a browser gesture should end the drag cleanly
    // rather than leave the panel stuck to the cursor.
    window.addEventListener("blur", stopDrag);

    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      window.removeEventListener("blur", stopDrag);
    };
  }, [dragging]);

  function stopDrag() {
    const start = grab.current;
    if (!start) return;
    grab.current = null;
    setParked(start.latest);
    setDragging(false);
  }

  /** Keep the panel readable for a while, then let it fade again. */
  function holdVisible() {
    window.clearTimeout(graceRef.current);
    setReading(true);
    graceRef.current = window.setTimeout(() => setReading(false), READ_GRACE_MS);
  }

  useEffect(() => () => window.clearTimeout(graceRef.current), []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, open]);

  /**
   * Take the reader to a target and ring it.
   *
   * Returns false when the element never mounted — a target that points at a
   * panel this page does not have, or one that failed to render. Saying so is
   * better than a silent no-op the reader reads as the assistant being wrong.
   */
  async function goTo(target: Target): Promise<boolean> {
    setTravellingTo(target.label);
    try {
      await navigate({
        to: target.route,
        params: target.params as never,
      });
      return await spotlight(target.anchor, target.label, target.guide ?? "");
    } catch {
      return false;
    } finally {
      setTravellingTo(null);
    }
  }

  async function send(event: React.FormEvent) {
    event.preventDefault();
    await ask(input);
  }

  async function ask(text: string) {
    const question = text.trim();
    if (!question || loading) return;

    const next: Msg[] = [...messages, { role: "user", content: question }];
    setMessages(next);
    setInput("");
    setLoading(true);

    try {
      const response = await fetch(apiUrl("/api/ai/assistant"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          // Only the prose goes back — the assistant has no use for the
          // targets it picked before, and they would only bloat the prompt.
          messages: next.map((m) => ({ role: m.role, content: m.content })),
          current_route: currentRoute,
        }),
      });

      const body = await response.json();

      if (!response.ok) {
        throw new Error(body?.detail ?? `Copilot unavailable (${response.status})`);
      }

      const target: Target | null = body.target ?? null;
      setMessages((prev) => [...prev, { role: "assistant", content: body.reply, target }]);
      holdVisible();

      if (target) {
        const landed = await goTo(target);
        if (!landed) {
          setMessages((prev) =>
            prev.map((m, i) => (i === prev.length - 1 ? { ...m, missed: true } : m)),
          );
        }
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: err instanceof Error ? err.message : "Copilot unavailable.",
        },
      ]);
      holdVisible();
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-[70] grid h-14 w-14 place-items-center rounded-full text-primary-foreground transition-all hover:scale-105 hover:opacity-100"
          style={{
            background: "var(--gradient-primary)",
            boxShadow: "var(--shadow-glow)",
            // Softer than the open panel: it is small, and it is the only way
            // back in, so it must never become hard to find.
            opacity: 0.55,
          }}
          aria-label="Open copilot"
        >
          <MessageCircle className="h-6 w-6" />
          <span className="absolute -right-1 -top-1 flex h-3 w-3">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />
            <span className="relative inline-flex h-3 w-3 rounded-full bg-primary" />
          </span>
        </button>
      )}

      {open && (
        <div
          ref={panelRef}
          // Above the spotlight overlay's dimming, so the explanation stays
          // legible while the thing it explains is lit up below.
          className="fixed bottom-6 right-6 z-[70] flex h-[560px] w-[92vw] max-w-sm flex-col overflow-hidden rounded-2xl border border-border/60 bg-card"
          style={{
            boxShadow: "var(--shadow-glow)",
            // The panel is opaque (bg-card), so fading it is what actually lets
            // the dashboard show through — nothing else needs to change.
            opacity: dimmed ? IDLE_OPACITY : 1,
            // Transform rather than repositioning: it composites, so both the
            // drag and the dodge stay smooth over a page that is also animating
            // a 3D scene.
            transform: `translate3d(${offset.x}px, ${offset.y}px, 0)`,
            // No transition while dragging, or the panel trails the pointer.
            transition: dragging
              ? "opacity 260ms ease-out"
              : "opacity 260ms ease-out, transform 420ms cubic-bezier(0.22, 0.9, 0.24, 1)",
          }}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          onFocusCapture={() => setFocused(true)}
          onBlurCapture={() => setFocused(false)}
        >
          <header
            className={`flex select-none items-center gap-2 border-b border-border/60 px-4 py-3 ${
              dragging ? "cursor-grabbing" : "cursor-grab"
            }`}
            onPointerDown={startDrag}
            // Somewhere to put it back, without having to aim for the corner.
            onDoubleClick={() => setParked(HOME)}
            title="Drag to move · double-click to reset"
          >
            <div
              className="grid h-8 w-8 place-items-center rounded-lg"
              style={{ background: "var(--gradient-primary)" }}
            >
              <Sparkles className="h-4 w-4 text-primary-foreground" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold">SkyPulse Copilot</div>
              <div className="text-[11px] text-muted-foreground">
                {dragging ? "Drop anywhere" : "Ask about the platform — I'll show you"}
              </div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => {
                // Closing the chat retires whatever it was pointing at; leaving
                // a highlight behind with no way to ask what it was is worse
                // than no highlight.
                dismissSpotlight();
                setOpen(false);
              }}
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </Button>
          </header>

          <div ref={scrollRef} className="flex-1 space-y-3 overflow-y-auto p-4">
            {messages.map((message, index) => (
              <div
                key={index}
                className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div className="max-w-[85%]">
                  <div
                    className={`rounded-2xl px-3 py-2 text-sm leading-relaxed ${
                      message.role === "user"
                        ? "rounded-br-sm bg-primary text-primary-foreground"
                        : "rounded-bl-sm bg-muted text-foreground"
                    }`}
                  >
                    {message.content}
                  </div>

                  {/* The jump already happened; this is how to repeat it after
                      the ring has faded or the reader has navigated away. */}
                  {message.target && (
                    <button
                      type="button"
                      onClick={() => goTo(message.target!)}
                      className="mt-1.5 flex items-center gap-1.5 rounded-lg border border-border/60 px-2 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary/60 hover:text-foreground"
                    >
                      <MapPin className="h-3 w-3 text-primary" />
                      {message.missed ? "Could not find it — try again" : message.target.label}
                    </button>
                  )}
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" /> Thinking…
              </div>
            )}

            {travellingTo && (
              <div className="flex items-center gap-2 text-xs text-primary">
                <Navigation className="h-3 w-3 animate-pulse" />
                Taking you to {travellingTo}…
              </div>
            )}

            {/* Only while the conversation is empty: the assistant can do more
                than answer questions about data, and nothing on screen says so. */}
            {messages.length === 1 && !loading && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    type="button"
                    onClick={() => ask(suggestion)}
                    className="rounded-full border border-border/60 px-2.5 py-1 text-[11px] text-muted-foreground transition-colors hover:border-primary/60 hover:text-foreground"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            )}
          </div>

          <form onSubmit={send} className="flex items-center gap-2 border-t border-border/60 p-3">
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about anything on screen…"
              disabled={loading}
            />
            <Button type="submit" size="icon" disabled={loading || !input.trim()}>
              <Send className="h-4 w-4" />
            </Button>
          </form>
        </div>
      )}
    </>
  );
}

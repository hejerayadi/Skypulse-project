import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { MousePointer2, Sparkles } from "lucide-react";
import {
  dismissSpotlight,
  setSpotlightArea,
  subscribeSpotlight,
  type SpotlightRequest,
} from "@/lib/spotlight";

/**
 * The "look here" overlay the copilot uses after taking someone somewhere.
 *
 * It is choreographed rather than instant, because the point is not just to
 * mark the target but to show the reader *how they would have got there*. A
 * pointer travels out of the chat, lands on the element and clicks it; the
 * page dims around it; then the explanation types itself out alongside.
 *
 * The typing is not decoration. The caption appears next to a panel the reader
 * has just been thrown to, and a paragraph that materialises all at once gets
 * skipped — a line that writes itself pulls the eye to the words at the same
 * speed it pulls the eye to the panel.
 *
 * The dimming is a single element with an enormous spread `box-shadow`, the
 * cheapest way to punch a hole in a backdrop: one node, no SVG mask, no
 * four-rectangle arithmetic.
 */

/** Breathing room between the cut-out and the element. */
const PAD = 8;

/** The pointer's flight, its press, and how long the caption lingers after. */
const TRAVEL_MS = 620;
const PRESS_MS = 320;
const LINGER_MS = 4200;

/** Milliseconds per character. Fast enough to read along with, not wait for. */
const TYPE_MS = 16;

const CALLOUT_WIDTH = 320;
/** Gap between the cut-out and the caption. */
const GAP = 14;
/** Keeps the caption off the very edge of the viewport. */
const MARGIN = 12;

type Phase = "travelling" | "pressing" | "revealed";
type Rect = { top: number; left: number; width: number; height: number };
type Point = { x: number; y: number };
type Placement = { top: number; left: number };

function measure(element: HTMLElement): Rect {
  const box = element.getBoundingClientRect();
  return {
    top: box.top - PAD,
    left: box.left - PAD,
    width: box.width + PAD * 2,
    height: box.height + PAD * 2,
  };
}

/** Where the pointer lands: the middle of the element, or its top area when
 *  the element is tall enough that its centre is off screen. */
function landingPoint(rect: Rect): Point {
  const height = window.innerHeight;
  const centreY = rect.top + rect.height / 2;
  return {
    x: rect.left + Math.min(rect.width / 2, 220),
    y: centreY > height * 0.85 ? rect.top + 48 : centreY,
  };
}

/**
 * Put the caption beside the target, not on top of it.
 *
 * Every side is clamped into the viewport and then *scored* by how much of the
 * target it would still cover, and the least-bad one wins. The obvious version
 * of this — try each side, return the first that fits — was wrong: when none
 * of them fit, the last one still gets clamped back into the viewport, and the
 * place it lands is squarely over the thing it is describing.
 *
 * Ties go to the earlier candidate, so a target with room on several sides
 * still gets the natural placement rather than an arbitrary one.
 */
function placeCallout(rect: Rect, height: number): Placement {
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  const clampTop = (top: number) =>
    Math.max(MARGIN, Math.min(top, Math.max(MARGIN, vh - height - MARGIN)));
  const clampLeft = (left: number) =>
    Math.max(MARGIN, Math.min(left, Math.max(MARGIN, vw - CALLOUT_WIDTH - MARGIN)));

  const candidates: Placement[] = [
    // Right, then left: preferred, because a caption alongside keeps the whole
    // target visible even when it is tall.
    { top: clampTop(rect.top), left: clampLeft(rect.left + rect.width + GAP) },
    { top: clampTop(rect.top), left: clampLeft(rect.left - GAP - CALLOUT_WIDTH) },
    // Below, then above: what a wide target leaves room for.
    { top: clampTop(rect.top + rect.height + GAP), left: clampLeft(rect.left) },
    { top: clampTop(rect.top - GAP - height), left: clampLeft(rect.left) },
    // Corners, for a target that fills most of the viewport and blocks all four
    // sides. Something has to overlap then; these overlap least.
    { top: clampTop(vh), left: clampLeft(vw) },
    { top: clampTop(0), left: clampLeft(vw) },
    { top: clampTop(vh), left: clampLeft(0) },
    { top: clampTop(0), left: clampLeft(0) },
  ];

  const coverage = (placement: Placement) => {
    const wide =
      Math.min(rect.left + rect.width, placement.left + CALLOUT_WIDTH) -
      Math.max(rect.left, placement.left);
    const tall =
      Math.min(rect.top + rect.height, placement.top + height) - Math.max(rect.top, placement.top);
    return Math.max(0, wide) * Math.max(0, tall);
  };

  let best = candidates[0];
  let bestCoverage = coverage(best);

  for (const candidate of candidates.slice(1)) {
    const covered = coverage(candidate);
    if (covered < bestCoverage) {
      best = candidate;
      bestCoverage = covered;
    }
    if (bestCoverage === 0) break;
  }

  return best;
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Reveal a string one character at a time.
 *
 * Skips straight to the end when the reader has asked for reduced motion, and
 * whenever `skip` flips — the caption must always be completable on demand
 * rather than something to sit and wait out.
 */
function useTypewriter(text: string, active: boolean, skip: boolean): string {
  const [shown, setShown] = useState("");

  useEffect(() => {
    if (!active) {
      setShown("");
      return;
    }
    if (skip || prefersReducedMotion()) {
      setShown(text);
      return;
    }

    setShown("");
    let index = 0;
    const id = window.setInterval(() => {
      index += 1;
      setShown(text.slice(0, index));
      if (index >= text.length) window.clearInterval(id);
    }, TYPE_MS);

    return () => window.clearInterval(id);
  }, [text, active, skip]);

  return shown;
}

export function SpotlightOverlay() {
  const [request, setRequest] = useState<SpotlightRequest | null>(null);
  const [rect, setRect] = useState<Rect | null>(null);
  const [phase, setPhase] = useState<Phase>("travelling");
  const [cursor, setCursor] = useState<Point | null>(null);
  const [skipTyping, setSkipTyping] = useState(false);
  const [calloutHeight, setCalloutHeight] = useState(96);

  const frameRef = useRef<number | undefined>(undefined);
  const calloutRef = useRef<HTMLDivElement>(null);

  useEffect(() => subscribeSpotlight(setRequest), []);

  const revealed = phase === "revealed";
  const guide = request?.guide ?? "";
  const typed = useTypewriter(guide, revealed, skipTyping);
  const typing = revealed && typed.length < guide.length;

  // Follow the element for as long as the highlight is up. A rAF loop rather
  // than scroll/resize listeners: the target also moves when a chart resizes or
  // a lazy panel above it finishes loading, and neither fires those events.
  useEffect(() => {
    if (!request) {
      setRect(null);
      setCursor(null);
      return;
    }

    const follow = () => {
      setRect(measure(request.element));
      frameRef.current = requestAnimationFrame(follow);
    };
    follow();

    return () => {
      if (frameRef.current !== undefined) cancelAnimationFrame(frameRef.current);
    };
  }, [request]);

  // Tell the rest of the app what is currently spoken for, so fixed furniture
  // — the copilot, mainly — can move out of the way.
  useEffect(() => {
    if (!rect) return;

    const placement = placeCallout(rect, calloutHeight);
    setSpotlightArea({
      top: Math.min(rect.top, placement.top),
      left: Math.min(rect.left, placement.left),
      right: Math.max(rect.left + rect.width, placement.left + CALLOUT_WIDTH),
      bottom: Math.max(rect.top + rect.height, placement.top + calloutHeight),
    });
  }, [rect, calloutHeight]);

  useEffect(() => () => setSpotlightArea(null), []);

  // Measured rather than guessed: the caption's height depends on how the
  // guide wraps, and placeCallout needs it to decide where it fits.
  //
  // Keyed on the *whole* guide rather than on what has been typed so far,
  // because the card reserves its full height from the first frame (see the
  // stacked paragraphs below). Measuring per character would re-place the card
  // on every keystroke and make it crawl across the screen as it wrote.
  useEffect(() => {
    if (calloutRef.current) setCalloutHeight(calloutRef.current.offsetHeight);
  }, [guide, request]);

  // The arrival sequence: fly in, press, reveal.
  useEffect(() => {
    if (!request) return;

    setSkipTyping(false);

    if (prefersReducedMotion()) {
      setPhase("revealed");
      setCursor(null);
      return;
    }

    setPhase("travelling");
    // Starts at the copilot, because that is where the answer came from — the
    // pointer reads as the assistant reaching out rather than a stray graphic.
    setCursor({ x: window.innerWidth - 64, y: window.innerHeight - 64 });

    // Next frame, so the element mounts at the start point and *then*
    // transitions. Setting both in one pass would teleport it.
    const raf = requestAnimationFrame(() => setCursor(landingPoint(measure(request.element))));

    const press = window.setTimeout(() => setPhase("pressing"), TRAVEL_MS);
    const reveal = window.setTimeout(() => setPhase("revealed"), TRAVEL_MS + PRESS_MS);

    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(press);
      window.clearTimeout(reveal);
    };
  }, [request]);

  // Auto-dismiss only once the caption has actually been readable for a while.
  // Timing it from the start would retire a long explanation mid-sentence.
  useEffect(() => {
    if (!request || typing) return;

    const timer = window.setTimeout(dismissSpotlight, LINGER_MS);
    return () => window.clearTimeout(timer);
  }, [request, typing]);

  useEffect(() => {
    if (!request) return;

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismissSpotlight();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [request]);

  if (!request || !rect || typeof document === "undefined") return null;

  const pressing = phase === "pressing";
  const landed = landingPoint(rect);
  const placement = placeCallout(rect, calloutHeight);

  return createPortal(
    <div className="pointer-events-none fixed inset-0 z-[60]" aria-live="polite">
      {/* The cut-out. The huge spread shadow is what dims everything else. */}
      <div
        className="absolute rounded-xl"
        style={{
          top: rect.top,
          left: rect.left,
          width: rect.width,
          height: rect.height,
          boxShadow: `0 0 0 9999px rgba(2, 6, 23, ${revealed ? 0.58 : 0}), 0 0 28px 6px color-mix(in oklab, var(--primary) ${revealed ? 45 : 0}%, transparent)`,
          border: `2px solid color-mix(in oklab, var(--primary) ${revealed ? 100 : 0}%, transparent)`,
          // Only the dimming is transitioned. Geometry must not be: the rAF
          // loop rewrites it every frame to stay glued to the element, and a
          // transition on top of that leaves the cut-out permanently lagging
          // behind whatever it is supposed to be framing.
          transition: "box-shadow 300ms ease-out, border-color 300ms ease-out",
        }}
      />

      {/* The click, expanding out of the spot the pointer landed on. */}
      {(pressing || revealed) && (
        <span
          className="absolute rounded-full border-2"
          style={{
            top: landed.y,
            left: landed.x,
            width: 24,
            height: 24,
            marginLeft: -12,
            marginTop: -12,
            borderColor: "var(--primary)",
            transform: `scale(${revealed ? 3.4 : 0.3})`,
            opacity: revealed ? 0 : 0.9,
            transition: "transform 520ms cubic-bezier(0.2, 0.8, 0.2, 1), opacity 520ms ease-out",
          }}
        />
      )}

      {/* The travelling pointer. */}
      {cursor && !revealed && (
        <MousePointer2
          className="absolute h-6 w-6 fill-primary text-primary-foreground drop-shadow-lg"
          style={{
            top: 0,
            left: 0,
            transform: `translate3d(${cursor.x}px, ${cursor.y}px, 0) scale(${pressing ? 0.8 : 1})`,
            transition: `transform ${TRAVEL_MS}ms cubic-bezier(0.35, 0.9, 0.25, 1)`,
          }}
        />
      )}

      {/* The explanation. The only part that takes clicks, so the reader can
          finish the typing or dismiss without the overlay swallowing the page
          underneath it. */}
      <div
        ref={calloutRef}
        className="pointer-events-auto absolute rounded-xl border border-primary/50 bg-popover/95 p-3 shadow-xl backdrop-blur-sm"
        style={{
          top: placement.top,
          left: placement.left,
          width: CALLOUT_WIDTH,
          opacity: revealed ? 1 : 0,
          transform: revealed ? "translateY(0)" : "translateY(6px)",
          transition: "opacity 240ms ease-out, transform 240ms cubic-bezier(0.2, 0.8, 0.2, 1)",
        }}
        onClick={() => (typing ? setSkipTyping(true) : dismissSpotlight())}
      >
        <div className="mb-1.5 flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5 shrink-0 text-primary" />
          <span className="text-xs font-semibold">{request.label}</span>
        </div>

        {/* The full text is laid out invisibly underneath the typed text, in
            the same grid cell, so the card is its final size from the first
            frame. Without it the card grows line by line as the text arrives
            and drifts away from whatever it is pointing at. */}
        <div className="grid text-xs leading-relaxed">
          <p aria-hidden className="invisible col-start-1 row-start-1">
            {guide}
          </p>
          <p className="col-start-1 row-start-1 text-foreground/90">
            {typed}
            {typing && <span className="ml-px inline-block animate-pulse">▍</span>}
          </p>
        </div>

        <div className="mt-2 text-right text-[10px] text-muted-foreground">
          {typing ? "click to skip" : "click to dismiss"}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/**
 * Find a part of the dashboard, take the reader to it, and show them.
 *
 * The copilot answers a question and names a place; this is the half that
 * actually gets them there. Elements are addressed by `data-tour` attributes
 * rather than CSS selectors or ids, so a component can be restyled or moved
 * without silently breaking the assistant.
 *
 * This module owns *getting there*; SpotlightOverlay owns *showing it*. The
 * split keeps the awkward part — the router and this code both want to decide
 * where the page is scrolled — in one readable place.
 */

export type SpotlightRequest = {
  anchor: string;
  label: string;
  /** The on-page explanation, typed out beside the element once it is lit. */
  guide: string;
  element: HTMLElement;
};

/** Screen space the highlight and its caption are using, in viewport pixels. */
export type SpotlightArea = { top: number; left: number; right: number; bottom: number };

/** How long to keep looking for the element after navigating. */
const WAIT_MS = 5000;

/**
 * Breathing room after the element mounts, before scrolling.
 *
 * The router is configured with `scrollRestoration: true`, which resets the
 * page to the top *after* the new route renders. Scrolling the instant the
 * element appears means the router undoes it a frame later and nothing
 * appears to happen at all — which is exactly the bug this delay fixes.
 */
const ROUTER_SETTLE_MS = 220;

/** Longest we wait for one smooth scroll to come to rest. */
const SCROLL_SETTLE_MS = 750;

/** Re-scrolls allowed when something moves the page out from under us. */
const SCROLL_ATTEMPTS = 3;

type Listener = (request: SpotlightRequest | null) => void;
type AreaListener = (area: SpotlightArea | null) => void;

const listeners = new Set<Listener>();
let active: SpotlightRequest | null = null;

export function subscribeSpotlight(listener: Listener): () => void {
  listeners.add(listener);
  listener(active);
  return () => listeners.delete(listener);
}

function publish(request: SpotlightRequest | null) {
  active = request;
  listeners.forEach((listener) => listener(request));
  if (!request) setSpotlightArea(null);
}

export function dismissSpotlight() {
  if (active) publish(null);
}

/**
 * Where the highlight currently is, so other fixed furniture can get out of
 * its way.
 *
 * Published by the overlay rather than computed here, because only the overlay
 * knows where the caption ended up — and a chat panel that dodged the element
 * but not its explanation would still be covering half of what it was told to
 * clear.
 */
const areaListeners = new Set<AreaListener>();
let area: SpotlightArea | null = null;

export function subscribeSpotlightArea(listener: AreaListener): () => void {
  areaListeners.add(listener);
  listener(area);
  return () => areaListeners.delete(listener);
}

export function setSpotlightArea(next: SpotlightArea | null) {
  const same =
    (next === null && area === null) ||
    (next !== null &&
      area !== null &&
      Math.abs(next.top - area.top) < 1 &&
      Math.abs(next.left - area.left) < 1 &&
      Math.abs(next.right - area.right) < 1 &&
      Math.abs(next.bottom - area.bottom) < 1);

  // The overlay recomputes this every animation frame; without this guard each
  // frame would re-render every subscriber.
  if (same) return;

  area = next;
  areaListeners.forEach((listener) => listener(next));
}

export function findAnchor(anchor: string): HTMLElement | null {
  if (typeof document === "undefined") return null;
  return document.querySelector<HTMLElement>(`[data-tour="${CSS.escape(anchor)}"]`);
}

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const nextFrame = () => new Promise((resolve) => requestAnimationFrame(resolve));

/**
 * Wait for an element to exist.
 *
 * Navigation resolves before the destination has rendered — the 3D scene is a
 * lazy chunk behind Suspense, and a sensor page has to load its data — so
 * looking once and giving up would miss most of the interesting targets. A
 * MutationObserver catches it the moment it mounts, with a deadline so a
 * mistargeted answer does not leave an observer running forever.
 */
function waitFor(anchor: string): Promise<HTMLElement | null> {
  const existing = findAnchor(anchor);
  if (existing) return Promise.resolve(existing);

  return new Promise((resolve) => {
    let settled = false;

    const finish = (element: HTMLElement | null) => {
      if (settled) return;
      settled = true;
      observer.disconnect();
      window.clearTimeout(deadline);
      resolve(element);
    };

    const observer = new MutationObserver(() => {
      const found = findAnchor(anchor);
      if (found) finish(found);
    });

    observer.observe(document.body, { childList: true, subtree: true });
    const deadline = window.setTimeout(() => finish(null), WAIT_MS);
  });
}

/** Is enough of the element on screen to call this "arrived"? */
function inView(element: HTMLElement): boolean {
  const box = element.getBoundingClientRect();
  const height = window.innerHeight;

  // Generous on purpose: a panel taller than the viewport can never be
  // centred, and demanding it would loop forever.
  return box.top < height * 0.75 && box.bottom > height * 0.15;
}

/**
 * Is it already exactly where a scroll would put it?
 *
 * Stricter than inView, and used only to skip scrolling altogether. Without
 * it, a target that is already centred still costs a full settle timeout
 * waiting for a scroll that never starts.
 */
function alreadyCentred(element: HTMLElement): boolean {
  const box = element.getBoundingClientRect();
  const height = window.innerHeight;
  const middle = box.top + Math.min(box.height, height) / 2;

  return Math.abs(middle - height / 2) < height * 0.12;
}

/** Let a smooth scroll finish. `scrollend` where it exists, a cap otherwise. */
function afterScroll(): Promise<void> {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      window.removeEventListener("scrollend", finish);
      window.clearTimeout(cap);
      resolve();
    };

    if ("onscrollend" in window) window.addEventListener("scrollend", finish, { once: true });
    const cap = window.setTimeout(finish, SCROLL_SETTLE_MS);
  });
}

/**
 * Scroll until the element is actually on screen.
 *
 * Checked and repeated rather than fired once: the router's scroll
 * restoration, a lazy panel above the target finishing its layout, or an image
 * loading can all move the page after the scroll starts. One attempt is what
 * made this silently do nothing; verifying is what makes it reliable.
 */
async function scrollTo(element: HTMLElement): Promise<void> {
  if (alreadyCentred(element)) return;

  for (let attempt = 0; attempt < SCROLL_ATTEMPTS; attempt++) {
    // Scroll first and check after, so there is always visible travel. The
    // reader is meant to watch the page move to the answer — jumping straight
    // to a highlight teaches them nothing about where it lives.
    element.scrollIntoView({ behavior: "smooth", block: "center" });
    await afterScroll();
    // One more frame so the final position is measurable.
    await nextFrame();

    if (inView(element)) return;
  }
}

/**
 * Bring one element to the reader's attention.
 *
 * Returns false when it never appeared, so the caller can say so rather than
 * pretending the jump worked.
 */
export async function spotlight(anchor: string, label = "Here", guide = ""): Promise<boolean> {
  const element = await waitFor(anchor);
  if (!element) {
    publish(null);
    return false;
  }

  // Clear any previous highlight before moving, so the old cut-out does not
  // sit on screen while the page scrolls away from it.
  publish(null);

  await nextFrame();
  await wait(ROUTER_SETTLE_MS);
  await scrollTo(element);

  publish({ anchor, label, guide, element });
  return true;
}

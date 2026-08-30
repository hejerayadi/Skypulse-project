import { Suspense, lazy, useEffect, useState } from "react";
import type { NodeStatus } from "@/lib/nodes";
import { ANTENNA_CREDIT, type ModelCredit } from "@/lib/sensor-models";
import { RFAlertPanel } from "@/components/rf-alert-panel";
import type { RFPrediction } from "@/lib/rf-fault";

// three.js and its React bindings are the largest dependency in the app and
// are useless during SSR, so the whole scene is a lazy chunk. This also keeps
// the module -- which touches WebGL at import time -- off the server render,
// the same constraint the Leaflet map worked around with a dynamic import.
const StationScene = lazy(() => import("@/components/station-scene"));

type Station3DProps = {
  nodes: NodeStatus[];
  selectedNodeId?: string | null;
  onSelect?: (nodeId: string) => void;
  /** Owned by the page, because the AI note beside the canvas reads it too. */
  prediction: RFPrediction | null;
  connected: boolean;
  rfError: string | null;
};

function Overlay({ children }: { children: React.ReactNode }) {
  return (
    <div className="pointer-events-none absolute inset-0 grid place-items-center rounded-xl bg-background/70 text-sm text-muted-foreground backdrop-blur-[1px]">
      {children}
    </div>
  );
}

/**
 * Attribution for the models currently on screen.
 *
 * The antenna is the only one left -- nodes are drawn as pads and beacons now,
 * so the per-role sensor models are no longer displayed and no longer need a
 * notice. CC-BY's condition is that the notice travels with the work, and
 * burying it in a source comment does not satisfy it, so this stays for as
 * long as the antenna is on screen. A `<details>` keeps it out of the way
 * without hiding it, and needs no state.
 */
function ModelCredits() {
  return (
    <details className="group absolute bottom-1.5 right-2 max-w-[min(22rem,calc(100%-1rem))] text-[10px]">
      <summary className="cursor-pointer list-none text-right text-muted-foreground/70 transition-colors hover:text-muted-foreground">
        3D model credits
      </summary>

      <ul className="mt-1 space-y-1 rounded-lg border border-border/60 bg-background/90 p-2 backdrop-blur-sm">
        <li className="text-muted-foreground">
          {ANTENNA_CREDIT ? <Credit credit={ANTENNA_CREDIT} /> : <>Antenna — source not recorded</>}
        </li>
      </ul>
    </details>
  );
}

function Credit({ credit }: { credit: ModelCredit }) {
  return (
    <>
      <a
        href={credit.modelUrl}
        target="_blank"
        rel="noopener noreferrer nofollow"
        className="font-medium text-foreground/80 underline-offset-2 hover:underline"
      >
        {credit.title}
      </a>{" "}
      by{" "}
      <a
        href={credit.authorUrl}
        target="_blank"
        rel="noopener noreferrer nofollow"
        className="underline-offset-2 hover:underline"
      >
        {credit.author}
      </a>{" "}
      · {credit.license}
    </>
  );
}

export function Station3D({
  nodes,
  selectedNodeId = null,
  onSelect,
  prediction,
  connected,
  rfError,
}: Station3DProps) {
  // `mounted` gates the canvas on the client: a WebGL context cannot be created
  // during the server pass, and hydrating one that never existed throws.
  const [mounted, setMounted] = useState(false);

  useEffect(() => setMounted(true), []);

  return (
    <div data-tour="station-3d" className="relative h-[26rem] w-full overflow-hidden rounded-xl">
      {mounted && (
        <Suspense fallback={null}>
          <StationScene
            nodes={nodes}
            selectedNodeId={selectedNodeId}
            onSelect={onSelect ?? (() => {})}
            prediction={prediction}
          />
        </Suspense>
      )}

      {!mounted && <Overlay>Loading ground station…</Overlay>}
      {mounted && nodes.length === 0 && <Overlay>No node has reported yet.</Overlay>}

      {mounted && <RFAlertPanel prediction={prediction} connected={connected} error={rfError} />}

      <ModelCredits />
    </div>
  );
}

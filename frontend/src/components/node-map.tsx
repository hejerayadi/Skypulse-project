import { useEffect, useRef, useState } from "react";
import type { CircleMarker, Map as LeafletMap } from "leaflet";
import { useTheme } from "@/components/theme-provider";
import { NODE_STATE_STYLE, formatAge, nodeRole, type NodeSite } from "@/lib/nodes";

type NodeMapProps = {
  sites: NodeSite[];
  focusSiteKey?: string | null;
  onSelect?: (siteKey: string) => void;
};

// One marker carries the whole site, so the tooltip has to answer "what is
// here?" — the node count, and what each node contributes.
function siteTooltip(site: NodeSite): string {
  const rows = site.nodes
    .map((node) => {
      const { color, label } = NODE_STATE_STYLE[node.state];
      return `<div style="display:flex;gap:6px;margin-top:4px">
        <span style="width:7px;height:7px;border-radius:9999px;background:${color};flex:none;margin-top:4px"></span>
        <span><strong>${node.node_id}</strong> · ${nodeRole(node)}<br/>
        <span style="opacity:.7">${label} · ${formatAge(node.seconds_since_seen)}</span></span>
      </div>`;
    })
    .join("");

  return `<strong>${site.nodes.length} node${site.nodes.length === 1 ? "" : "s"} at this site</strong>${rows}`;
}

const WORLD_VIEW: [number, number] = [20, 0];
const WORLD_ZOOM = 2;

export function NodeMap({ sites, focusSiteKey, onSelect }: NodeMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const leafletRef = useRef<typeof import("leaflet") | null>(null);
  const markersRef = useRef(new Map<string, CircleMarker>());
  const onSelectRef = useRef(onSelect);
  const hasFramedRef = useRef(false);
  const [ready, setReady] = useState(false);
  const { theme } = useTheme();

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    let cancelled = false;
    let map: LeafletMap | null = null;

    async function initMap() {
      // Leaflet reads `window` while it is being imported, so it can only be
      // pulled in from the browser -- a top-level import breaks the SSR render.
      const L = (await import("leaflet")).default;
      await import("leaflet/dist/leaflet.css");

      if (cancelled || !containerRef.current) return;

      map = L.map(containerRef.current, {
        worldCopyJump: true,
        minZoom: 2,
      }).setView(WORLD_VIEW, WORLD_ZOOM);

      // OpenStreetMap's own tile servers: free, no key, attribution required.
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }).addTo(map);

      leafletRef.current = L;
      mapRef.current = map;
      setReady(true);
    }

    initMap();

    return () => {
      cancelled = true;
      map?.remove();
      mapRef.current = null;
      leafletRef.current = null;
      markersRef.current.clear();
      hasFramedRef.current = false;
      setReady(false);
    };
  }, []);

  // The sidebar collapsing changes the map's width without a window resize,
  // which leaves Leaflet drawing tiles for the old size.
  useEffect(() => {
    if (!ready || !containerRef.current) return;

    const observer = new ResizeObserver(() => mapRef.current?.invalidateSize());
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [ready]);

  useEffect(() => {
    const L = leafletRef.current;
    const map = mapRef.current;
    if (!L || !map) return;

    const markers = markersRef.current;

    for (const site of sites) {
      const color = NODE_STATE_STYLE[site.state].color;

      let marker = markers.get(site.key);
      if (!marker) {
        // Radius grows with the node count so a busy site reads as bigger than
        // a lone one, capped so it stays a marker rather than a blob.
        marker = L.circleMarker([site.latitude, site.longitude], {
          radius: Math.min(8 + site.nodes.length, 16),
          weight: 2,
        }).addTo(map);
        marker.on("click", () => onSelectRef.current?.(site.key));
        markers.set(site.key, marker);
      } else {
        marker.setLatLng([site.latitude, site.longitude]);
        marker.setRadius(Math.min(8 + site.nodes.length, 16));
      }

      marker.setStyle({ color, fillColor: color, fillOpacity: 0.7 });
      marker.bindTooltip(siteTooltip(site), { direction: "top", offset: [0, -6] });
    }

    const live = new Set(sites.map((site) => site.key));
    for (const [siteKey, marker] of markers) {
      if (live.has(siteKey)) continue;
      marker.remove();
      markers.delete(siteKey);
    }

    // Frame the fleet once, on the first poll that returns positions; after that
    // the user's own pan/zoom is left alone.
    if (!hasFramedRef.current && sites.length > 0) {
      hasFramedRef.current = true;
      map.fitBounds(L.latLngBounds(sites.map((site) => [site.latitude, site.longitude])), {
        padding: [48, 48],
        maxZoom: 8,
      });
    }
  }, [sites, ready]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !focusSiteKey) return;

    const site = sites.find((candidate) => candidate.key === focusSiteKey);
    if (!site) return;

    map.flyTo([site.latitude, site.longitude], Math.max(map.getZoom(), 9), { duration: 0.6 });
    markersRef.current.get(focusSiteKey)?.openTooltip();
    // `sites` is intentionally excluded: re-centring on every poll would fight
    // the user for control of the map.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusSiteKey, ready]);

  return (
    <div className="relative">
      <div
        ref={containerRef}
        className={`skypulse-map h-[26rem] w-full rounded-xl ${theme === "dark" ? "skypulse-map--dark" : ""}`}
      />
      {(!ready || sites.length === 0) && (
        <div className="pointer-events-none absolute inset-0 grid place-items-center rounded-xl bg-background/70 text-sm text-muted-foreground backdrop-blur-[1px]">
          {!ready ? "Loading map…" : "No node has reported a GPS position yet."}
        </div>
      )}
    </div>
  );
}

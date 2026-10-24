// Click-and-drag a box on the map. Pattern from GeoLibre's maplibre-satellite-embeddings.ts (MIT).
import type { Bbox } from "../engine/crs";

const SRC = "ecd-area", FILL = "ecd-area-fill", LINE = "ecd-area-line";

const ring = ([w, s, e, n]: Bbox) => [[w, s], [e, s], [e, n], [w, n], [w, s]];

/** Show (or with null, hide) the area outline. */
export function showArea(map: any, bbox: Bbox | null): void {
  if (!map?.style?._loaded) return;              // mid basemap swap: step 1 re-shows it on style.load
  if (!map.getSource(SRC)) map.addSource(SRC, { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  if (!map.getLayer(FILL)) map.addLayer({ id: FILL, type: "fill", source: SRC, paint: { "fill-color": "#ffd700", "fill-opacity": 0.06 } });
  if (!map.getLayer(LINE)) map.addLayer({ id: LINE, type: "line", source: SRC, paint: { "line-color": "#ffd700", "line-width": 2, "line-dasharray": [2, 1] } });
  map.getSource(SRC).setData({
    type: "FeatureCollection",
    features: bbox ? [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring(bbox)] } }] : [],
  });
}

export function removeArea(map: any): void {
  if (!map) return;
  for (const id of [LINE, FILL]) if (map.getLayer(id)) map.removeLayer(id);
  if (map.getSource(SRC)) map.removeSource(SRC);
}

/** Start drawing; panning is off until the box is done. Returns a cancel function. */
export function drawBox(map: any, onDone: (bbox: Bbox) => void): () => void {
  const canvas = map.getCanvas();
  canvas.style.cursor = "crosshair";
  // Hides the before/after lens while drawing: it sits on the cursor, over the very box being drawn.
  map.getContainer?.()?.classList.add("ecd-drawing");
  map.dragPan.disable();
  map.boxZoom.disable();
  let start: { lng: number; lat: number } | null = null;
  const box = (a: { lng: number; lat: number }, b: { lng: number; lat: number }): Bbox => {
    const west = Math.min(a.lng, b.lng);
    const shift = (((west + 180) % 360) + 360) % 360 - 180 - west;    // back onto the main world copy
    return [west + shift, Math.min(a.lat, b.lat), Math.min(Math.max(a.lng, b.lng) + shift, 180), Math.max(a.lat, b.lat)];
  };
  // Move and release are followed on the WINDOW, not the map: let go over a map button, the
  // panel or past the map's edge and the map never hears the mouseup, so the box stayed half
  // drawn and never finished. Outside the map the corner is clamped to its edge.
  const at = (e: MouseEvent) => {
    const r = canvas.getBoundingClientRect();
    const x = Math.min(Math.max(e.clientX - r.left, 0), r.width), y = Math.min(Math.max(e.clientY - r.top, 0), r.height);
    return map.unproject([x, y]);
  };
  const down = (e: any) => { start = e.lngLat; };
  const move = (e: MouseEvent) => { if (start) showArea(map, box(start, at(e))); };
  const cleanup = () => {
    map.off("mousedown", down);
    window.removeEventListener("mousemove", move);
    window.removeEventListener("mouseup", up);
    canvas.style.cursor = "";
    map.getContainer?.()?.classList.remove("ecd-drawing");
    map.dragPan.enable();
    map.boxZoom.enable();
  };
  function up(e: MouseEvent) {
    if (!start) return;
    const b = box(start, at(e));
    start = null;
    if (b[0] === b[2] || b[1] === b[3]) return;                 // a click, not a drag
    cleanup();
    showArea(map, b);
    onDone(b);
  }
  map.on("mousedown", down);
  window.addEventListener("mousemove", move);
  window.addEventListener("mouseup", up);
  return cleanup;
}

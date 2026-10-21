// Development only: a MapLibre map plus just enough of GeoLibre's app API to run the plugin.
// MapLibre comes from its CDN in index.html: its own map workers do not start under the dev server.
const maplibregl = (window as any).maplibregl;
import { plugin } from "../src/index";

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: { osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "OpenStreetMap" } },
    layers: [{ id: "osm", type: "raster", source: "osm" }],
  },
  center: [-125.29, 49.67],
  zoom: 11,
});
(window as any).map = map;
const side = document.getElementById("side")!;
const layers: Record<string, unknown> = {};
(window as any).registered = layers;
const app: any = {
  getMap: () => map,
  registerRightPanel(p: any) {
    const h = document.createElement("h2");
    h.textContent = p.title;
    const body = document.createElement("div");
    side.append(h, body);
    const dispose = p.render(body);
    return () => { dispose?.(); h.remove(); body.remove(); };
  },
  openRightPanel() {}, closeRightPanel() {},
  registerExternalNativeLayer: (r: any) => { layers[r.id] = r; },
  unregisterExternalNativeLayer: (id: string) => { delete layers[id]; },
  exportTextFile: (name: string) => console.log("exportTextFile", name),
};
plugin.activate(app);

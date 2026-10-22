// Development only: a MapLibre map plus just enough of GeoLibre's app API to run the plugin.
// MapLibre comes from its CDN in index.html: its own map workers do not start under the dev server.
const maplibregl = (window as any).maplibregl;
import { plugin } from "../src/index";
import { sessionRuns } from "../src/runs";
import * as runfile from "../src/runfile";
import * as job from "../src/engine/job";
import * as objects from "../src/engine/objects";
(window as any).ecd = { sessionRuns, runfile, job, objects };   // for checks from the console

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
(window as any).appApi = () => app;
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
  // Layer groups and zoom, as GeoLibre's app API offers them (just recorded here).
  groups: [] as { id: string; name: string }[],
  addLayerGroup(name: string) { const id = `g${app.groups.length + 1}`; app.groups.push({ id, name }); return id; },
  listLayerGroups: () => app.groups.map((g: any) => ({ ...g, parentId: null, visible: true, opacity: 1, collapsed: false })),
  moveLayersToGroup(ids: string[], gid: string) { for (const id of ids) if (layers[id]) (layers[id] as any).groupId = gid; },
  removeLayerGroup(id: string) { app.groups = app.groups.filter((g: any) => g.id !== id); },
  fitBounds: (b: [number, number, number, number]) => map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 20 }),
};
plugin.activate(app);

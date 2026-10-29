import { turbo, scoreColour, splitWindow, coverageColour, tileCorners, ChangeLayer, CHANGE_OPACITY } from "../src/map/changelayer";
import { transformer } from "../src/engine/crs";
import type { Grid } from "../src/engine/grid";

test("the score ramp is Turbo (blue, cyan, green, yellow, red) over 0..0.5, whatever the cutoff", () => {
  expect(turbo(0.5)).toEqual([150, 250, 80]);                  // the fit's green-yellow middle
  expect(scoreColour(0.0001, -1)).toEqual([...turbo(0.1), 255]); // starts at blue, not near-black
  expect(scoreColour(0.25, 0)).toEqual([...turbo(0.55), 255]);  // halfway
  expect(scoreColour(0.25, 0.2)).toEqual(scoreColour(0.25, 0)); // moving the cutoff keeps the colour
  expect(scoreColour(0.5, 0)).toEqual([...turbo(1), 255]);
  expect(scoreColour(1.0, 0)).toEqual([...turbo(1), 255]);
  expect(CHANGE_OPACITY).toBe(0.85);
});

test("at or below the cutoff, and nodata, are transparent", () => {
  expect(scoreColour(0.3, 0.3)[3]).toBe(0);
  expect(scoreColour(0.31, 0.3)[3]).toBe(255);
  expect(scoreColour(-1, 0)[3]).toBe(0);
});

test("coverage colours match the QGIS legend", () => {
  expect([0, 1, 2, 3, 4].map(coverageColour)).toEqual([
    [45, 50, 60, 60], [0, 0, 0, 0], [214, 60, 200, 220], [60, 170, 235, 220], [110, 115, 125, 170]]);
});

test("tile corners are the window corners in lon/lat", () => {
  const g: Grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 300, height: 300 };
  const toLL = transformer("EPSG:32610", "EPSG:4326");
  const c = tileCorners(g, [10, 110, 20, 220]);
  const want = [toLL(500200, 5499900), toLL(502200, 5499900), toLL(502200, 5498900), toLL(500200, 5498900)];
  c.forEach((p, i) => p.forEach((v, j) => expect(v).toBeCloseTo(want[i][j], 9)));
});

test("three cutoff changes in one frame recolour once, with the last value", () => {
  let frame: (() => void) | null = null;
  const map: any = {
    addSource: vi.fn(), addLayer: vi.fn(), getLayer: () => true, removeLayer: vi.fn(), removeSource: vi.fn(),
    getSource: () => ({ play: vi.fn(), pause: vi.fn() }), once: (_: string, f: () => void) => f(), triggerRepaint: vi.fn(),
    setPaintProperty: vi.fn(), setLayoutProperty: vi.fn(),
  };
  const app: any = { getMap: () => map, registerExternalNativeLayer: vi.fn(), unregisterExternalNativeLayer: vi.fn() };
  const layer = new ChangeLayer(app, "run1", "2019-2024", (f) => { frame = f; });
  const recolour = vi.spyOn(layer as any, "recolourAll");
  layer.setCutoff(0.1); layer.setCutoff(0.2); layer.setCutoff(0.3);
  expect(recolour).not.toHaveBeenCalled();
  frame!();
  expect(recolour).toHaveBeenCalledTimes(1);
  expect((layer as any).cutoff).toBe(0.3);
});

test("setting the cutoff it already has recolours nothing: a run re-sends it with every tile", () => {
  const frames: (() => void)[] = [];
  const map: any = { getSource: () => undefined, once() {}, triggerRepaint() {} };
  const layer = new ChangeLayer({ getMap: () => map } as any, "run1", "2019-2024", (f) => { frames.push(f); });
  layer.setCutoff(0.2);
  frames.splice(0).forEach((f) => f());
  layer.setCutoff(0.2);                                  // tile 2, 3, ... of the same run
  layer.setCutoff(0.2);
  expect(frames).toHaveLength(0);
});

test("tiles wait for the style, and come back after a basemap switch replaces it", () => {
  const handlers: Record<string, (() => void)[]> = {};
  const sources = new Set<string>();
  const map: any = {
    style: { _loaded: false },
    on: (ev: string, f: () => void) => (handlers[ev] ??= []).push(f),
    off: (ev: string, f: () => void) => (handlers[ev] = (handlers[ev] ?? []).filter((g) => g !== f)),
    once: (_: string, f: () => void) => f(),
    addSource: (id: string) => { if (!map.style._loaded) throw new Error("Style is not done loading"); sources.add(id); },
    addLayer: () => {}, getLayer: () => false, getSource: (id: string) => (sources.has(id) ? { play() {}, pause() {} } : undefined),
    removeLayer: () => {}, removeSource: (id: string) => sources.delete(id), triggerRepaint: () => {},
  };
  const app: any = { getMap: () => map, registerExternalNativeLayer: () => {}, unregisterExternalNativeLayer: () => {} };
  const layer = new ChangeLayer(app, "r", "2019-2024", (f) => f());
  const g: Grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 2, height: 1 };
  layer.addTile(g, { window: [0, 1, 0, 2], score: Float32Array.from([0.5, -1]), cov: Uint8Array.from([1, 4]), hist: new Float64Array(256) });
  expect(sources.size).toBe(0);                       // style not ready: nothing added, nothing thrown
  map.style._loaded = true;
  handlers["style.load"].forEach((f) => f());
  expect([...sources].sort()).toEqual(["ecd-r-change-0", "ecd-r-coverage-0", "ecd-r-extent"]);
  sources.clear();                                     // a basemap switch wipes the style
  handlers["style.load"].forEach((f) => f());
  expect(sources.size).toBe(3);
  layer.clear();
  expect(handlers["style.load"].length).toBe(0);
});

test("all three layers tell GeoLibre their lon/lat extent, so Zoom to layer works", () => {
  const reg: any[] = [];
  const map: any = { style: { _loaded: true }, on() {}, off() {}, addSource() {}, addLayer() {}, getLayer: () => false,
    getSource: () => undefined, triggerRepaint() {} };
  const app: any = { getMap: () => map, registerExternalNativeLayer: (r: any) => reg.push(r) };
  const layer = new ChangeLayer(app, "r", "2019-2024", (f) => f());
  const g: Grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 200, height: 100 };
  layer.addTile(g, { window: [0, 100, 0, 200], score: new Float32Array(20000), cov: new Uint8Array(20000), hist: new Float64Array(256) });
  const toLL = transformer("EPSG:32610", "EPSG:4326");
  const [w, n] = toLL(500000, 5500000), [e, s] = toLL(502000, 5499000);
  for (const r of reg) {
    const b = r.metadata.bounds;
    expect(b[0]).toBeLessThanOrEqual(w + 1e-9); expect(b[1]).toBeLessThanOrEqual(s + 1e-9);
    expect(b[2]).toBeGreaterThanOrEqual(e - 1e-9); expect(b[3]).toBeGreaterThanOrEqual(n - 1e-9);
    expect(b[2] - b[0]).toBeLessThan(0.05);
  }
  expect(reg.map((r) => r.id).sort()).toEqual(["ecd-r-change", "ecd-r-coverage", "ecd-r-extent"]);
});

test("each tile's canvas reaches one pixel into its right and bottom neighbour (no hairline between tiles)", () => {
  const map: any = {
    style: { _loaded: true }, on() {}, off() {}, once: (_: string, f: () => void) => f(), triggerRepaint() {},
    addSource: vi.fn(), addLayer() {}, getLayer: () => false, getSource: () => undefined, setPaintProperty() {}, setLayoutProperty() {},
  };
  const app: any = { getMap: () => map, registerExternalNativeLayer() {}, unregisterExternalNativeLayer() {} };
  const layer = new ChangeLayer(app, "r", "2019-2024", (f) => f());
  const g: Grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 4, height: 2 };
  const tile = (c0: number, c1: number) => ({ window: [0, 2, c0, c1] as any, score: new Float32Array(2 * (c1 - c0)).fill(0.5),
    cov: new Uint8Array(2 * (c1 - c0)).fill(1), hist: new Float64Array(256) });
  layer.addTile(g, tile(0, 2));
  layer.addTile(g, tile(2, 4));
  const canvases = map.addSource.mock.calls.filter((c: any[]) => c[0].includes("change")).map((c: any[]) => c[1].canvas);
  expect(canvases.map((c: HTMLCanvasElement) => [c.width, c.height])).toEqual([[3, 2], [2, 2]]);   // the last column is the grid's edge
});

test("big tiles are split so the change map stays on the objects; 10 m tiles are not", () => {
  // North Vancouver Island, UTM 10 (the grid of a whole-island run), a full 1024-pixel tile.
  const at = (res: number): Grid => ({ crs: "EPSG:32610", x0: 260000, y0: 5600000, res, width: 1024, height: 1024 });
  expect(splitWindow(at(10), [0, 1024, 0, 1024])).toEqual([[0, 1024, 0, 1024]]);
  const pieces = splitWindow(at(75), [0, 546, 0, 546]);       // 75 m Detail: 40 m tiles, 41 km
  expect(pieces.length).toBe(4);
  expect(splitWindow(at(160), [0, 1024, 0, 1024]).length).toBe(16);
  // The pieces cover the window exactly once.
  const n = pieces.reduce((a, [r0, r1, c0, c1]) => a + (r1 - r0) * (c1 - c0), 0);
  expect(n).toBe(546 * 546);
  expect(pieces[3]).toEqual([273, 546, 273, 546]);
});

test("each piece is stretched flat, not in perspective", () => {
  const warps: string[] = [];
  const map: any = { style: { _loaded: true }, on() {}, off() {}, addSource() {}, addLayer() {}, getLayer: () => false,
    triggerRepaint() {}, once() {} };
  const added = new Set<string>();
  map.getSource = (id: string) => (added.has(id) ? { setWarp: (w: string) => warps.push(w), play() {}, pause() {} } : undefined);
  map.addSource = (id: string) => { added.add(id); };
  const layer = new ChangeLayer({ getMap: () => map } as any, "r", "x", (f) => f());
  const g: Grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 2, height: 2 };
  layer.addTile(g, { window: [0, 2, 0, 2], score: new Float32Array(4).fill(0.5), cov: new Uint8Array(4).fill(1), hist: new Float64Array(256) } as any);
  expect(warps).toEqual(["flat", "flat"]);
});

test("the run's extent is an outline of the grid's corners, in the run's group, and goes with the run", () => {
  const sources = new Map<string, any>(), layers: any[] = [], regs: any[] = [], gone: string[] = [];
  const map: any = {
    style: { _loaded: true }, on() {}, off() {}, once: (_: string, f: () => void) => f(), triggerRepaint() {},
    addSource: (id: string, src: any) => sources.set(id, src), getSource: (id: string) => sources.has(id) ? { play() {}, pause() {} } : undefined,
    addLayer: (l: any) => layers.push(l), getLayer: (id: string) => layers.some((l) => l.id === id),
    removeLayer: (id: string) => gone.push(id), removeSource: (id: string) => sources.delete(id),
  };
  const app: any = { getMap: () => map, registerExternalNativeLayer: (r: any) => regs.push(r), unregisterExternalNativeLayer() {} };
  const layer = new ChangeLayer(app, "r", "2019-2024", (f) => f());
  layer.groupId = "g1";
  const g: Grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 2, height: 1 };
  layer.addTile(g, { window: [0, 1, 0, 2], score: Float32Array.from([0.5, 0.5]), cov: Uint8Array.from([1, 1]), hist: new Float64Array(256) });
  const line = sources.get("ecd-r-extent").data.geometry.coordinates;
  expect(line).toHaveLength(5);
  expect(line[4]).toEqual(line[0]);
  expect(line[0]).toEqual(tileCorners(g, [0, 1, 0, 2])[0]);
  expect(layers.filter((l) => l.source === "ecd-r-extent").every((l) => l.type === "line")).toBe(true);   // no fill
  expect(regs.find((r) => r.id === "ecd-r-extent")).toMatchObject({ name: "Extent 2019-2024", groupId: "g1" });
  layer.clear();
  expect(sources.has("ecd-r-extent")).toBe(false);
  expect(gone).toContain("ecd-r-extent-line");
});

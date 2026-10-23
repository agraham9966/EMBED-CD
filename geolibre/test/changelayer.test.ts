import { scoreColour, scoreColours, coverageColour, tileCorners, ChangeLayer, CHANGE_OPACITY } from "../src/map/changelayer";
import { transformer } from "../src/engine/crs";
import type { Grid } from "../src/engine/grid";

test("the score ramp matches the QGIS layer", () => {
  expect(scoreColour(0.0, -1)).toEqual([0, 210, 235, 255]);
  expect(scoreColour(0.2, -1)).toEqual([128, 218, 138, 255]);
  expect(scoreColour(0.4, -1)).toEqual([255, 225, 40, 255]);
  expect(scoreColour(0.8, -1)).toEqual([235, 30, 30, 255]);
  expect(scoreColour(1.0, -1)).toEqual([235, 30, 30, 255]);
  expect(CHANGE_OPACITY).toBe(0.85);
});

test("at or below the cutoff, and nodata, are transparent", () => {
  expect(scoreColour(0.3, 0.3)[3]).toBe(0);
  expect(scoreColour(0.31, 0.3)[3]).toBe(255);
  expect(scoreColour(-1, 0)[3]).toBe(0);
});

test("coverage colours match the QGIS legend", () => {
  expect([0, 1, 2, 3, 4].map(coverageColour)).toEqual([
    [45, 50, 60, 60], [0, 0, 0, 0], [214, 60, 200, 220], [60, 170, 235, 220], [255, 255, 255, 230]]);
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
  expect([...sources].sort()).toEqual(["ecd-r-change-0", "ecd-r-coverage-0"]);
  sources.clear();                                     // a basemap switch wipes the style
  handlers["style.load"].forEach((f) => f());
  expect(sources.size).toBe(2);
  layer.clear();
  expect(handlers["style.load"].length).toBe(0);
});

test("both layers tell GeoLibre their lon/lat extent, so Zoom to layer works", () => {
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
  expect(reg.map((r) => r.id).sort()).toEqual(["ecd-r-change", "ecd-r-coverage"]);
});

test("the precomputed colours match the ramp, and a cutoff only hides pixels", () => {
  const score = Float32Array.from([-1, 0, 0.2, 0.4, 0.8, 3]);
  const px = [...scoreColours(score)].map((u) => [u & 255, (u >>> 8) & 255, (u >>> 16) & 255, u >>> 24]);
  expect(px).toEqual([[0, 0, 0, 0], ...[0, 0.2, 0.4, 0.8, 3].map((v) => scoreColour(v, -1))]);
});

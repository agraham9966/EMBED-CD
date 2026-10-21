import { scoreColour, coverageColour, tileCorners, ChangeLayer, CHANGE_OPACITY } from "../src/map/changelayer";
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

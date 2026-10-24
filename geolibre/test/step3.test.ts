import { objectsGeoJSON } from "../src/map/objectslayer";
import { countText, makeTicker, Cancelled } from "../src/ui/step3";
import { transformer } from "../src/engine/crs";

test("objects become lon/lat GeoJSON with their numbers", () => {
  const grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 10, height: 10 };
  const ring: [number, number][] = [[500000, 5500000], [500000, 5499990], [500010, 5499990], [500010, 5500000], [500000, 5500000]];
  const fc = objectsGeoJSON([{ rings: [ring], areaHa: 0.01, chgMean: 0.4, chgMax: 0.5, nPx: 1 }], grid);
  const toLL = transformer("EPSG:32610", "EPSG:4326");
  expect(fc.features[0].properties).toEqual({ id: 1, area_ha: 0.01, chg_mean: 0.4, chg_max: 0.5, n_px: 1, predicted: "" });
  const got = (fc.features[0].geometry as any).coordinates[0][2];
  const want = toLL(500010, 5499990);
  expect(got[0]).toBeCloseTo(want[0], 9);
  expect(got[1]).toBeCloseTo(want[1], 9);
});

test("the count line", () => {
  expect(countText(37, 0.2)).toBe("37 objects at cutoff 0.20.");
});

test("cutting yields to the panel every 50 ms of work, and Cancel stops it", async () => {
  let now = 0, yields = 0, cancelled = false;
  const tick = makeTicker(() => now, async () => { yields++; }, () => cancelled);
  await tick(); expect(yields).toBe(0);
  now = 49; await tick(); expect(yields).toBe(0);
  now = 51; await tick(); expect(yields).toBe(1);
  now = 120; await tick(); expect(yields).toBe(2);
  cancelled = true;
  await expect(tick()).rejects.toBeInstanceOf(Cancelled);
});

test("switching runs points at that run's objects; the other run's objects stay on the map", async () => {
  const { SessionRuns } = await import("../src/runs");
  const { buildStep3 } = await import("../src/ui/step3");
  const added: string[] = [], removed: string[] = [];
  const map: any = { style: { _loaded: true }, on() {}, off() {}, getSource: (id: string) => (added.includes(id) && !removed.includes(id) ? { setData() {} } : undefined),
    addSource: (id: string) => added.push(id), addLayer() {}, getLayer: () => true, removeLayer() {}, removeSource: (id: string) => removed.push(id),
    setPaintProperty() {}, setFilter() {} };
  const app: any = { getMap: () => map, registerExternalNativeLayer() {}, unregisterExternalNativeLayer() {} };
  const runs = new SessionRuns();
  const grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 1, height: 1 };
  const ring: [number, number][] = [[500000, 5500000], [500000, 5499990], [500010, 5499990], [500010, 5500000], [500000, 5500000]];
  runs.start({ id: "a", grid, yearA: 2019, yearB: 2024 } as any);
  runs.setObjects("a", { polys: [{ rings: [ring], areaHa: 0.01, chgMean: 0.5, chgMax: 0.5, nPx: 1 }], vectors: new Float32Array(128), cutoff: 0.3, minArea: 0 });
  runs.start({ id: "b", grid, yearA: 2019, yearB: 2024 } as any);
  const s3 = buildStep3({ app, runs, currentRun: () => null, cutoff: () => 0.3 });
  s3.runChanged("a");
  expect(added).toEqual(["ecd-a-objects"]);
  expect(s3.root.textContent).toContain("1 objects at cutoff 0.30.");
  s3.runChanged("b");
  expect(removed).toEqual([]);
  expect(s3.root.textContent).not.toContain("objects at cutoff");
  s3.runChanged("a");
  expect(added).toEqual(["ecd-a-objects"]);            // reused, not added twice
});

test("after cutting, the change map is switched off so only the outlines show (as in QGIS)", async () => {
  const { SessionRuns } = await import("../src/runs");
  const { buildStep3 } = await import("../src/ui/step3");
  const map: any = { style: { _loaded: true }, on() {}, off() {}, getSource: () => undefined, addSource() {},
    addLayer() {}, getLayer: () => false, removeLayer() {}, removeSource() {} };
  const hidden: string[] = [];
  const app: any = { getMap: () => map, registerExternalNativeLayer() {}, unregisterExternalNativeLayer() {} };
  const runs = new SessionRuns();
  const grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 2, height: 1 };
  const cells = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 160, width: 1, height: 1,
    ma: new Float32Array(64).fill(0.1), mb: new Float32Array(64).fill(0.2), n: Float32Array.from([2]), smean: Float32Array.from([0.9]), smax: Float32Array.from([0.9]) };
  runs.start({ id: "r", grid, yearA: 2019, yearB: 2024 } as any);
  runs.addTile("r", { window: [0, 1, 0, 2], score: Float32Array.from([0.9, 0.9]), cov: Uint8Array.from([1, 1]), hist: new Float64Array(256), cells });
  const s3 = buildStep3({ app, runs, currentRun: () => "r", cutoff: () => 0.5, onObjectsMade: () => hidden.push("change") });
  (s3.root.querySelector("input") as HTMLInputElement).value = "0";
  (s3.root.querySelector("button") as HTMLButtonElement).click();
  await new Promise((r) => setTimeout(r, 50));
  expect(s3.root.textContent).toContain("1 objects at cutoff 0.50.");
  expect(hidden).toEqual(["change"]);
});

test("outlines are drawn a hair thicker than before (2.6 px)", async () => {
  const { ObjectsLayer } = await import("../src/map/objectslayer");
  const layers: any[] = [];
  const map: any = { style: { _loaded: true }, on() {}, off() {}, getSource: () => undefined, addSource() {}, addLayer: (l: any) => layers.push(l) };
  new ObjectsLayer({ getMap: () => map } as any, "r", "x").set([], { crs: "EPSG:32610", x0: 0, y0: 0, res: 10, width: 1, height: 1 });
  expect(layers.find((l) => l.id.endsWith("-line")).paint["line-width"].at(-1)).toBe(2.6);   // not classified yet
});

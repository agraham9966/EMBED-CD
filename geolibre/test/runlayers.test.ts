import { SessionRuns } from "../src/runs";
import { buildStep1 } from "../src/ui/step1";
import { buildStep2 } from "../src/ui/step2";

function harness() {
  const sources = new Set<string>(), added: string[] = [], regs: any[] = [], groups: { id: string; name: string }[] = [];
  const map: any = {
    style: { _loaded: true }, on() {}, off() {}, once: (_: string, f: () => void) => f(), triggerRepaint() {},
    getSource: (id: string) => (sources.has(id) ? { setData() {}, play() {}, pause() {} } : undefined),
    addSource: (id: string) => { sources.add(id); added.push(id); }, addLayer() {}, getLayer: () => true,
    removeLayer() {}, removeSource: (id: string) => sources.delete(id),
    getCanvas: () => ({ style: {} }), dragPan: { disable() {}, enable() {} }, boxZoom: { disable() {}, enable() {} },
  };
  const app: any = {
    getMap: () => map,
    registerExternalNativeLayer: (r: any) => regs.push(r), unregisterExternalNativeLayer() {},
    addLayerGroup: (name: string) => { const id = `g${groups.length + 1}`; groups.push({ id, name }); return id; },
    listLayerGroups: () => groups.map((g) => ({ ...g, parentId: null, visible: true, opacity: 1, collapsed: false })),
  };
  const grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 2, height: 1 };
  const startJob = (_spec: any, on: (e: any) => void) => {
    queueMicrotask(() => {
      on({ type: "plan", grid, tiles: 1, partial: 0, gb: 0 });
      on({ type: "tile", done: 1, total: 1, result: { window: [0, 1, 0, 2], score: Float32Array.from([0.5, 0.6]), cov: Uint8Array.from([1, 1]), hist: new Float64Array(256) } });
      on({ type: "end", histTotal: new Float64Array(256), failed: 0, cancelled: false });
    });
    return { cancel() {}, done: new Promise<void>((r) => setTimeout(r, 0)) };
  };
  const runs = new SessionRuns();
  const s1 = buildStep1({ app, runs, step2: buildStep2(() => {}), loadIndex: () => new Promise(() => {}), startJob });
  return { s1, runs, sources, added, regs, groups, setArea: (s1 as any).__setArea };
}

async function makeRun(h: ReturnType<typeof harness>, bbox: [number, number, number, number]) {
  (h.s1 as any).setArea(bbox);
  const run = [...h.s1.root.querySelectorAll("button")].find((b) => b.textContent === "Make change map")!;
  run.disabled = false;
  run.click();
  await new Promise((r) => setTimeout(r, 20));
}

test("a second run keeps the first run's layers; each run has its own group", async () => {
  const h = harness();
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);
  const first = [...h.sources];
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);           // same area, same years, no name
  for (const id of first) expect(h.sources.has(id)).toBe(true);
  expect(h.groups.map((g) => g.name)).toEqual(["49.67N 125.29W  2019→2024", "49.67N 125.29W  2019→2024 (2)"]);
  const [r2, r1] = h.runs.list();
  expect(h.regs.filter((r) => r.id.includes(r1.id)).every((r) => r.groupId === r1.groupId)).toBe(true);
  expect(h.regs.filter((r) => r.id.includes(r2.id)).every((r) => r.groupId === r2.groupId)).toBe(true);
});

test("switching back to a run reuses its layers instead of adding them again", async () => {
  const h = harness();
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);
  await makeRun(h, [-125.0, 49.0, -124.9, 49.1]);
  const before = h.added.length;
  const picker = h.s1.root.querySelector("select")!;
  picker.value = h.runs.list()[1].id;
  picker.dispatchEvent(new Event("change"));
  expect(h.added.length).toBe(before);
});

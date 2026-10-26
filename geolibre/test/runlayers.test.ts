import { SessionRuns } from "../src/runs";
import { buildStep1 } from "../src/ui/step1";
import { buildStep2 } from "../src/ui/step2";

function harness() {
  const sources = new Set<string>(), added: string[] = [], regs: any[] = [], groups: { id: string; name: string }[] = [];
  const map: any = {
    style: { _loaded: true }, on() {}, off() {}, once: (_: string, f: () => void) => f(), triggerRepaint() {},
    getSource: (id: string) => (sources.has(id) ? { setData: (d: any) => data.set(id, d), play() {}, pause() {} } : undefined),
    addSource: (id: string) => { sources.add(id); added.push(id); }, getLayer: () => true,
    addLayer: (l: { id: string }) => seq.push(`add ${l.id}`), moveLayer: (id: string) => seq.push(`top ${id}`),
    removeLayer() {}, removeSource: (id: string) => sources.delete(id),
    getCanvas: () => ({ style: {} }), dragPan: { disable() {}, enable() {} }, boxZoom: { disable() {}, enable() {} },
  };
  const app: any = {
    getMap: () => map,
    registerExternalNativeLayer: (r: any) => { regs.push(r); store.add(r.id); },
    unregisterExternalNativeLayer: (id: string) => store.delete(id),
    listLayers: () => [...store].map((id) => ({ id })),
    addLayerGroup: (name: string) => { const id = `g${groups.length + 1}`; groups.push({ id, name }); return id; },
    listLayerGroups: () => groups.map((g) => ({ ...g, parentId: null, visible: true, opacity: 1, collapsed: false })),
    moveLayersToGroup: (ids: string[], gid: string) => moved.push([ids, gid]),
    removeLayerGroup: (id: string) => { removedGroups.push(id); groups.splice(groups.findIndex((g) => g.id === id), 1); },
  };
  const moved: [string[], string][] = [], removedGroups: string[] = [], store = new Set<string>(), seq: string[] = [], data = new Map<string, any>();
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
  return { s1, runs, sources, added, regs, groups, moved, removedGroups, store, seq, data };
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

test("naming: a typed name names the next run's group; editing it renames the current run and moves its layers", async () => {
  const h = harness();
  const moves: [string[], string][] = [], removedGroups: string[] = [];
  (h as any).s1; const app = (h.s1 as any);
  const name = h.s1.root.querySelector("input.ecd-text") as HTMLInputElement;
  (h.s1 as any).setArea([-125.34, 49.63, -125.24, 49.71]);
  expect(name.placeholder).toBe("49.67N 125.29W");
  name.value = "Nanaimo";
  name.dispatchEvent(new Event("change"));
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);
  expect(h.groups.at(-1)!.name).toBe("Nanaimo  2019→2024");
  const picker = h.s1.root.querySelector("select")!;
  expect(picker.options[picker.selectedIndex].text).toBe("Nanaimo · 2019→2024 · 10 m (full)");
  void app; void moves; void removedGroups;
  name.value = "Nanaimo south";
  name.dispatchEvent(new Event("change"));
  expect(h.groups.at(-1)!.name).toBe("Nanaimo south  2019→2024");
  const run = h.runs.list()[0];
  expect(run.group).toBe("Nanaimo south  2019→2024");
  expect(h.moved.at(-1)).toEqual([expect.arrayContaining([`ecd-${run.id}-change`, `ecd-${run.id}-coverage`]), run.groupId]);
  expect(h.removedGroups).toEqual(["g1"]);
  expect(picker.options[picker.selectedIndex].text).toBe("Nanaimo south · 2019→2024 · 10 m (full)");
});

test("progress and Cancel live outside step 1, so they stay visible when the steps move on (as in QGIS)", async () => {
  const h = harness();
  (h.s1 as any).setArea([-125.34, 49.63, -125.24, 49.71]);
  const row = h.s1.progressRow;
  expect(h.s1.root.contains(row)).toBe(false);
  const runBtn = [...h.s1.root.querySelectorAll("button")].find((b) => b.textContent === "Make change map")!;
  const cancel = [...row.querySelectorAll("button")].find((b) => b.textContent === "Cancel")!;
  expect(cancel.hidden).toBe(true);
  runBtn.disabled = false;
  runBtn.click();
  expect(runBtn.disabled).toBe(true);
  expect(cancel.hidden).toBe(false);
  expect((row.querySelector("progress") as HTMLElement).hidden).toBe(false);
  await new Promise((r) => setTimeout(r, 20));
  expect(cancel.hidden).toBe(true);
  expect(row.textContent).toContain("Done.");
});

test("a second area: a name typed before Draw area is the new area's, the last run keeps its own, and step 1 has no result yet", async () => {
  const h = harness();
  document.body.append(h.s1.root);
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);
  expect(h.s1.hasResult()).toBe(true);
  const name = h.s1.root.querySelector("input.ecd-text") as HTMLInputElement;
  const draw = [...h.s1.root.querySelectorAll("button")].find((b) => b.textContent === "Draw area")!;
  name.focus();
  name.value = "Second";
  draw.dispatchEvent(new Event("pointerdown"));      // comes before the field's change event
  name.dispatchEvent(new Event("change"));
  expect(h.runs.list()[0].userName).toBe("");        // the first run is not renamed
  h.s1.setArea([-125.0, 49.0, -124.9, 49.1]);
  expect(name.value).toBe("Second");
  expect(h.s1.current()).toBe(null);
  expect(h.s1.hasResult()).toBe(false);              // so the panel does not fold step 1 away
  h.s1.root.remove();
});

test("layers removed in the Layers panel stay removed; the run stays in Area and choosing it puts them back", async () => {
  const h = harness();
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);
  await makeRun(h, [-125.0, 49.0, -124.9, 49.1]);
  const [, first] = h.runs.list();
  const picker = h.s1.root.querySelector("select")!;
  // The user deletes the first run's group: its layers and the group go.
  h.store.delete(`ecd-${first.id}-change`);
  h.store.delete(`ecd-${first.id}-coverage`);
  h.groups.splice(h.groups.findIndex((g) => g.id === first.groupId), 1);
  picker.dispatchEvent(new Event("pointerdown"));
  const opt = [...picker.options].find((o) => o.value === first.id)!;
  expect(opt.text).toBe("49.67N 125.29W · 2019→2024 · 10 m (full) (off the map)");
  const regs = h.regs.length;
  picker.value = first.id;
  picker.dispatchEvent(new Event("change"));
  expect(h.store.has(`ecd-${first.id}-change`)).toBe(true);
  expect(h.regs.length).toBeGreaterThan(regs);
  expect(h.groups.some((g) => g.id === first.groupId)).toBe(true);      // a new group for it
  picker.dispatchEvent(new Event("pointerdown"));
  expect(picker.options[picker.selectedIndex].text).not.toContain("off the map");
});

test("Discard run: an unsaved run asks first, then leaves the Area list, the map and the Layers panel", async () => {
  const h = harness();
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);
  const run = h.runs.list()[0];
  const discard = [...h.s1.root.querySelectorAll("button")].find((b) => b.textContent === "Discard run")!;
  expect(discard.disabled).toBe(false);
  discard.click();
  expect(h.runs.list()).toHaveLength(1);
  // The question is on the button itself, where you are looking, not in the status line below.
  expect(discard.textContent).toBe("Click again to discard");
  expect(discard.classList.contains("ecd-danger")).toBe(true);
  discard.click();
  expect(h.runs.list()).toEqual([]);
  expect(h.store.size).toBe(0);
  expect(h.removedGroups).toContain(run.groupId);
  expect(h.s1.current()).toBe(null);
  expect(discard.disabled).toBe(true);
  expect(discard.textContent).toBe("Discard run");
  expect(discard.classList.contains("ecd-danger")).toBe(false);
});

test("Discard run asks for 5 s, then goes back to asking", async () => {
  const h = harness();
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);
  vi.useFakeTimers();
  const discard = [...h.s1.root.querySelectorAll("button")].find((b) => b.textContent === "Discard run")!;
  discard.click();
  vi.advanceTimersByTime(5001);
  expect(discard.textContent).toBe("Discard run");
  discard.click();                                      // asks again: a late click never discards
  expect(h.runs.list()).toHaveLength(1);
  vi.useRealTimers();
});

test("the drawn box shows until the run starts; then the run's own extent layer replaces it", async () => {
  const h = harness();
  (h.s1 as any).setArea([-125.34, 49.63, -125.24, 49.71]);
  expect(h.data.get("ecd-area").features).toHaveLength(1);
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);
  expect(h.data.get("ecd-area").features).toHaveLength(0);
  expect(h.regs.some((r) => r.id.endsWith("-extent"))).toBe(true);
});

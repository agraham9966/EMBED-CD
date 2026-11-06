import { packTile } from "../src/runfolder";
import { SessionRuns } from "../src/runs";
import { buildStep1 } from "../src/ui/step1";
import { buildStep2 } from "../src/ui/step2";

function harness(extra: Record<string, unknown> = {}) {
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
    ...extra,
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
  const startJob = (spec: any, on: (e: any) => void) => {
    queueMicrotask(() => {
      on({ type: "plan", grid, tiles: 1, partial: 0, gb: 0 });
      on({ type: "tile", done: 1, total: 1, packed: spec.pack ? ((extra.packed as Uint8Array) ?? new Uint8Array(8)) : undefined, result: { window: [0, 1, 0, 2], score: Float32Array.from([0.5, 0.6]), cov: Uint8Array.from([1, 1]), hist: new Float64Array(256) } });
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

test("Save to folder: only offered where the host can do it, and a run writes itself there", async () => {
  const saveRow = (h: ReturnType<typeof harness>) =>
    [...h.s1.root.querySelectorAll<HTMLElement>(".ecd-row")].find((r) => r.textContent?.startsWith("Save to"))!;
  expect(saveRow(harness()).hidden).toBe(true);
  const written = new Map<string, unknown>();
  let fail = false;
  const h = harness({
    pickDownloadFolder: async () => ({ id: "f1", path: "D:/runs" }),
    pickLocalDirectoryFiles: async () => null,
    writeFileToFolder: async (id: string, name: string, data: unknown) => {
      expect(id).toBe("f1");
      if (fail) throw new Error("disk full");
      written.set(name, data);
    },
  });
  const button = (t: string) => [...h.s1.root.querySelectorAll("button")].find((b) => b.textContent === t)!;
  expect(saveRow(h).hidden).toBe(false);
  button("...").click();
  await new Promise((r) => setTimeout(r, 0));
  expect(h.s1.root.textContent).toContain("D:/runs");
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);
  const names = [...written.keys()];
  expect(names.some((n) => n.endsWith("_run.json"))).toBe(true);
  expect(names.some((n) => n.endsWith("_t0001_p1of1.part"))).toBe(true);
  const manifest = JSON.parse(written.get(names.find((n) => n.endsWith("_run.json"))!) as string);
  expect(manifest.meta).toMatchObject({ complete: true, folder: "D:/runs", tiles: 1 });
  expect(h.s1.progressRow.textContent).toContain("Saved to D:/runs.");

  fail = true;                                          // the disk fills up: the run itself is fine
  await makeRun(h, [-125.0, 49.63, -124.9, 49.71]);
  expect(h.runs.list()).toHaveLength(2);
  expect(h.s1.progressRow.textContent).toContain("Saving to the folder stopped: disk full");

  button("stop saving to a folder").click();            // saveFolder lasts the session: leave it clear
  expect(h.s1.root.textContent).not.toContain("D:/runs");
});

test("a run saved to a folder: Discard needs one click, Open folder brings it back, and it keeps saving", async () => {
  const score = Float32Array.from([0.5, 0.6]), cov = Uint8Array.from([1, 1]);
  const packed = await packTile({ window: [0, 1, 0, 2], score, cov, hist: new Float64Array(256) });
  const disk = new Map<string, Uint8Array>();
  const h = harness({
    packed,
    pickDownloadFolder: async () => ({ id: "f1", path: "D:/runs" }),
    pickLocalDirectoryFiles: async () => [...disk].map(([n, b]) => new File([b as BlobPart], n)),
    writeFileToFolder: async (_id: string, name: string, data: string | Uint8Array) => {
      disk.set(name, typeof data === "string" ? new TextEncoder().encode(data) : data.slice());
    },
  });
  const button = (t: string) => [...h.s1.root.querySelectorAll("button")].find((b) => b.textContent === t)!;
  const settle = () => new Promise((r) => setTimeout(r, 10));
  button("...").click();
  await settle();
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);
  await settle();
  const id = h.runs.list()[0].id;

  button("Discard run").click();                        // everything is in the folder: no question
  expect(h.runs.list()).toEqual([]);

  button("Open run...").click();
  await new Promise((r) => setTimeout(r, 50));
  expect(h.s1.progressRow.textContent).toContain("Opened 1 tiles.");
  expect(h.s1.progressRow.textContent).not.toContain("set Save to");
  expect(h.runs.get(id)!.tiles[0].cov.length).toBe(2);
  expect(h.runs.get(id)!.writer).toBeTruthy();          // its folder is the one picked: saving goes on

  button("stop saving to a folder").click();
  h.runs.get(id)!.writer = undefined;
  button("Open run...").click();                        // the same run again, folder not picked
  await new Promise((r) => setTimeout(r, 50));
  expect(h.s1.progressRow.textContent).toContain("To keep saving changes, set Save to to D:/runs.");
  button("...").click();
  await settle();
  expect(h.runs.get(id)!.writer).toBeTruthy();          // picking it resumes
  button("stop saving to a folder").click();
});

test("Open run lists a folder's runs and run files, and one click opens the run chosen", async () => {
  const score = Float32Array.from([0.5, 0.6]), cov = Uint8Array.from([1, 1]);
  const packed = await packTile({ window: [0, 1, 0, 2], score, cov, hist: new Float64Array(256) });
  const disk = new Map<string, Uint8Array>();
  const subfolders = new Set<unknown>();
  const h = harness({
    packed,
    pickDownloadFolder: async () => ({ id: "f1", path: "D:/runs" }),
    pickLocalDirectoryFiles: async () => [...disk].map(([n, b]) => new File([b as BlobPart], n)),
    writeFileToFolder: async (_id: string, name: string, data: string | Uint8Array, o: { subfolder?: string }) => {
      subfolders.add(o?.subfolder);
      disk.set(name, typeof data === "string" ? new TextEncoder().encode(data) : data.slice());
    },
  });
  const button = (t: string) => [...h.s1.root.querySelectorAll("button")].find((b) => b.textContent === t)!;
  const settle = (ms = 10) => new Promise((r) => setTimeout(r, ms));
  expect(button("Open run...").parentElement!.textContent).toContain("Save to");    // one Open, by the folder
  button("...").click();
  await settle();
  await makeRun(h, [-125.34, 49.63, -125.24, 49.71]);
  await settle();
  await settle(1100);                                   // run names go by the second
  await makeRun(h, [-120.34, 49.63, -120.24, 49.71]);
  await settle();
  expect(subfolders.size).toBe(2);                      // a folder per run
  expect([...subfolders].every((n) => /^embed-cd_/.test(String(n)))).toBe(true);
  disk.set("embed-cd_2019-2024_10m_2026-11-05.zip", new Uint8Array(4));
  disk.set("holiday.zip", new Uint8Array(4));

  button("Open run...").click();
  await settle(50);
  const list = h.s1.root.querySelector(".ecd-runlist") as HTMLElement;
  expect(list.hidden).toBe(false);
  expect(list.textContent).toContain("3 saved runs in that folder:");
  expect(list.textContent).toContain("embed-cd_2019-2024_10m_2026-11-05.zip");
  expect(list.textContent).not.toContain("holiday");

  button("Cancel").click();
  expect(list.hidden).toBe(true);

  button("Open run...").click();
  await settle(50);
  list.querySelector("button")!.click();
  await settle(50);
  expect(list.hidden).toBe(true);
  expect(h.s1.progressRow.textContent).toContain("Opened 1 tiles.");
});

test("a name typed before the box stays, even after clicking elsewhere, and names the run and its folder", async () => {
  const folders: unknown[] = [];
  const h = harness({
    pickDownloadFolder: async () => ({ id: "f1", path: "D:/runs" }),
    pickLocalDirectoryFiles: async () => null,
    writeFileToFolder: async (_id: string, _name: string, _data: unknown, o: { subfolder?: string }) => { folders.push(o?.subfolder); },
  });
  const name = h.s1.root.querySelector("input.ecd-text") as HTMLInputElement;
  [...h.s1.root.querySelectorAll("button")].find((b) => b.textContent === "...")!.click();
  await new Promise((r) => setTimeout(r, 0));
  name.value = "Van Island";
  name.dispatchEvent(new Event("change"));              // clicked away: focus left the field
  h.s1.setArea([-125.0, 49.0, -124.9, 49.1]);
  expect(name.value).toBe("Van Island");
  h.s1.setArea([-125.5, 49.0, -125.4, 49.1]);           // redrawn before running: still this name
  expect(name.value).toBe("Van Island");
  await makeRun(h, [-125.5, 49.0, -125.4, 49.1]);
  expect(h.runs.list()[0].userName).toBe("Van Island");
  expect(String(folders[0])).toMatch(/^embed-cd_Van-Island_2019-2024_/);
  h.s1.setArea([-124.0, 49.0, -123.9, 49.1]);           // the next area: that name was the run's
  expect(name.value).toBe("");
});

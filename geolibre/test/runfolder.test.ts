import { FolderWriter, listFolderRuns, loadFolderRun, packTile, PART_BYTES, runPrefix, unpackTile } from "../src/runfolder";
import type { TileResult } from "../src/engine/job";
import type { RunRecord } from "../src/runs";

const grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 4, height: 2 };
const meta = (id = "run-1"): RunRecord => ({ id, name: "2019 to 2024, 10 m, 4 Nov 2026", createdAt: Date.UTC(2026, 10, 4, 15, 30, 12),
  bbox: [-123, 49.6, -122.9, 49.7], yearA: 2019, yearB: 2024, resM: 10, grid, histTotal: [], complete: false, failed: 0, tiles: 0 });
const cells = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 160, width: 1, height: 1,
  ma: new Int16Array(64).fill(7), mb: new Int16Array(64).fill(-9), n: Float32Array.from([2]), smean: Float32Array.from([0.5]), smax: Float32Array.from([0.75]) };
const tile = (c0 = 0): TileResult => ({ window: [0, 2, c0, c0 + 2], score: Float32Array.from([0.125, -1, 0.5, 0.25]),
  cov: Uint8Array.from([1, 2, 1, 1]), hist: new Float64Array(256).fill(1), cells });

/** A folder on the host: name -> bytes, written one at a time. */
function fakeFolder() {
  const disk = new Map<string, Uint8Array>();
  let busy = 0, most = 0;
  const write = async (name: string, data: string | Uint8Array) => {
    most = Math.max(most, ++busy);
    await Promise.resolve();
    disk.set(name, typeof data === "string" ? new TextEncoder().encode(data) : data.slice());
    busy--;
  };
  const files = () => [...disk].map(([n, b]) => new File([b as BlobPart], n));
  return { disk, write, files, most: () => most };
}

test("a tile comes back whole: map, coverage, histogram and embedding cells", async () => {
  const t = await unpackTile(await packTile(tile()));
  expect(t.window).toEqual([0, 2, 0, 2]);
  expect([...t.score]).toEqual([0.125, -1, 0.5, 0.25]);
  expect([...t.cov]).toEqual([1, 2, 1, 1]);
  expect(t.hist.length).toBe(256);
  expect(t.cells).toMatchObject({ crs: "EPSG:32610", res: 160, width: 1, height: 1 });
  expect(t.cells!.ma).toBeInstanceOf(Int16Array);
  expect([...t.cells!.mb.slice(0, 2)]).toEqual([-9, -9]);
  expect([...t.cells!.smax]).toEqual([0.75]);
});

test("the prefix is unique per run, and legal in a file name", () => {
  expect(runPrefix(meta())).toMatch(/^embed-cd_2019-2024_10m_2026-11-04_\d{6}$/);
});

test("the prefix carries the run's name, made safe for a file name, and the stamp keeps same-named runs apart", () => {
  const named = (userName: string, createdAt = meta().createdAt) => runPrefix({ ...meta(), userName, createdAt });
  expect(named("Van Island")).toMatch(/^embed-cd_Van-Island_2019-2024_10m_2026-11-04_\d{6}$/);
  expect(named("  a/b: c_d?  ")).toMatch(/^embed-cd_a-b-c-d_2019-2024_/);
  expect(named("\u6625\u306e\u5730\u56f3")).toMatch(/^embed-cd_\u6625\u306e\u5730\u56f3_2019/);
  expect(named("???")).toBe(runPrefix(meta()));          // nothing usable: as if unnamed
  expect(named("clearcut", 1000)).not.toBe(named("clearcut", 2000));
  const longest = `${named("x".repeat(500))}_o1793901217162_p999of999.part`;
  expect(longest.length).toBeLessThanOrEqual(120);       // the host refuses longer names
});

test("a run's files are found by the prefix they were written with, whatever it is called now", async () => {
  const f = fakeFolder();
  const w = new FolderWriter(f.write, "embed-cd_old-name_2019-2024_10m_2026-11-04_153012");
  w.manifest({ ...meta(), userName: "new name" });
  w.tile(1, await packTile(tile()));
  await w.done();
  const [found] = await listFolderRuns(f.files());
  expect(found.tiles).toBe(1);
  expect((await loadFolderRun(found)).meta.prefix).toBe("embed-cd_old-name_2019-2024_10m_2026-11-04_153012");
});

test("writes go one at a time, in small parts, and a run reads back from the folder", async () => {
  const f = fakeFolder();
  const w = new FolderWriter(f.write, "pre");
  const m = meta();
  w.manifest(m);
  const big = new Uint8Array(PART_BYTES * 2 + 5).fill(3);
  w.tile(2, big);
  await w.done();
  expect(f.most()).toBe(1);
  expect([...f.disk.keys()].sort()).toEqual(["pre_run.json", "pre_t0002_p1of3.part", "pre_t0002_p2of3.part", "pre_t0002_p3of3.part"]);
  expect(Math.max(...[...f.disk.values()].map((b) => b.length))).toBeLessThanOrEqual(PART_BYTES);
});

test("a whole run survives the folder, and a tile cut off by a crash is left out", async () => {
  const f = fakeFolder();
  const w = new FolderWriter(f.write, "pre");
  const m = { ...meta(), folder: "D:/runs" };
  w.manifest(m);
  w.tile(1, await packTile(tile(0)));
  w.tile(2, await packTile(tile(2)));
  await w.done();
  f.disk.set("pre_t0003_p1of2.part", new Uint8Array(9));         // the crash: part 2 never landed
  const [found] = await listFolderRuns(f.files());
  expect(found.meta.id).toBe("run-1");
  expect(found.tiles).toBe(2);
  const { meta: back, tiles } = await loadFolderRun(found);
  expect(tiles.map((t) => t.window)).toEqual([[0, 2, 0, 2], [0, 2, 2, 4]]);
  expect(back.tiles).toBe(2);
  expect(back.complete).toBe(false);
  expect(tiles[1].cells).toBeTruthy();
});

test("several runs can share a folder, and other files there are ignored", async () => {
  const f = fakeFolder();
  for (const [prefix, id] of [["a", "run-a"], ["b", "run-b"]]) {
    const w = new FolderWriter(f.write, prefix);
    w.manifest(meta(id));
    w.tile(1, await packTile(tile()));
    await w.done();
  }
  f.disk.set("notes.txt", new Uint8Array(3));
  f.disk.set("broken_run.json", new TextEncoder().encode("{not json"));
  const found = await listFolderRuns(f.files());
  expect(found.map((r) => r.meta.id).sort()).toEqual(["run-a", "run-b"]);
  expect(found.every((r) => r.tiles === 1)).toBe(true);
});

test("a failed write stops the saving, says why, and never throws into the run", async () => {
  const failed: string[] = [];
  let calls = 0;
  const w = new FolderWriter(async () => { calls++; throw new Error("disk full"); }, "pre", (why) => failed.push(why));
  w.tile(1, new Uint8Array(4));
  w.tile(2, new Uint8Array(4));
  w.manifest(meta());
  await w.done();
  expect(calls).toBe(1);
  expect(failed).toEqual(["disk full"]);
  expect(w.failed).toBe("disk full");
});

const ring: [number, number][] = [[500000, 5500000], [500000, 5499990], [500010, 5499990], [500000, 5500000]];
const objects = (n = 2, cutoff = 0.3) => ({ polys: Array.from({ length: n }, () => ({ rings: [ring], areaHa: 1, chgMean: 0.4, chgMax: 0.5, nPx: 3 })),
  vectors: new Float32Array(n * 128).fill(0.25), cutoff, minArea: 1 });
const labels = (row = 1) => ({ classes: ["clearcut"], colors: { clearcut: "#d85a30" }, banked: { clearcut: [[0.5]] },
  labels: [[row, "clearcut"]] as [number, string][], features: "after" as const });

describe("objects and labels", () => {
  beforeEach(() => { vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] }); });
  afterEach(() => { vi.useRealTimers(); });

  async function settle(w: FolderWriter) {
    await vi.advanceTimersByTimeAsync(1100);
    await w.done();
  }

  it("come back with the run, cutoff included", async () => {
    const f = fakeFolder();
    const w = new FolderWriter(f.write, "pre");
    w.manifest(meta());
    w.tile(1, await packTile(tile()));
    w.objects(objects(2, 0.31));
    w.labels(labels(1));
    await settle(w);
    const [found] = await listFolderRuns(f.files());
    const back = await loadFolderRun(found);
    expect(back.objects!.polys).toHaveLength(2);
    expect(back.objects!.cutoff).toBe(0.31);
    expect(back.objects!.vectors.length).toBe(256);
    expect(back.objects!.vectors[255]).toBe(0.25);
    expect(back.labelState).toEqual(labels(1));
    expect(back.meta.objectsStamp).toBe(found.objectsStamp);
  });

  it("many label clicks make one write, a moment after they stop", async () => {
    const f = fakeFolder();
    const w = new FolderWriter(f.write, "pre");
    let writes = 0;
    const counted = new FolderWriter(async (n, d) => { writes++; await f.write(n, d); }, "pre");
    for (let i = 0; i < 20; i++) counted.labels(labels(i));
    await settle(counted);
    expect(writes).toBe(1);
    expect(JSON.parse(new TextDecoder().decode(f.disk.get("pre_labels.json"))).state.labels).toEqual([[19, "clearcut"]]);
    await w.done();
  });

  it("Generate again: the newest whole set is read, and old labels keep only their classes", async () => {
    const f = fakeFolder();
    const w = new FolderWriter(f.write, "pre");
    w.manifest(meta());
    w.objects(objects(2));
    w.labels(labels(1));
    await settle(w);
    vi.setSystemTime(Date.now() + 5000);
    w.objects(objects(5));                               // no labels written for these yet: the crash
    await w.done();
    const back = await loadFolderRun((await listFolderRuns(f.files()))[0]);
    expect(back.objects!.polys).toHaveLength(5);
    expect(back.labelState!.labels).toEqual([]);         // those rows meant the older objects
    expect(back.labelState!.classes).toEqual(["clearcut"]);
    expect(back.labelState!.banked).toEqual({ clearcut: [[0.5]] });
  });

  it("a set cut off by a crash is passed over for the last whole one", async () => {
    const f = fakeFolder();
    const w = new FolderWriter(f.write, "pre");
    w.manifest(meta());
    w.objects(objects(2));
    await settle(w);
    f.disk.set("pre_o9999999999999_p1of2.part", new Uint8Array(4));
    const back = await loadFolderRun((await listFolderRuns(f.files()))[0]);
    expect(back.objects!.polys).toHaveLength(2);
  });

  it("damaged labels do not stop the run opening", async () => {
    const f = fakeFolder();
    const w = new FolderWriter(f.write, "pre");
    w.manifest(meta());
    w.objects(objects(2));
    await settle(w);
    f.disk.set("pre_labels.json", new TextEncoder().encode("{not json"));
    const back = await loadFolderRun((await listFolderRuns(f.files()))[0]);
    expect(back.objects!.polys).toHaveLength(2);
    expect(back.labelState).toBeUndefined();
  });
});

import { startJob, type ClientEvent } from "../src/jobclient";
import { createHandler } from "../src/workerlogic";
import type { TileIndex } from "../src/engine/tileindex";
import { fxJson, fixtureRow, openFixture } from "./fixtures";

const J = fxJson("job.json");
const rows = [fixtureRow("cog_a.tif", 2019), fixtureRow("cog_b.tif", 2024)];
const index = {
  covering: (_b: unknown, y: number) => rows.filter((r) => r.year === y),
  rowFor: (_t: unknown, y: number) => rows.find((r) => r.year === y) ?? null,
} as unknown as TileIndex;

/** A Worker stand-in that runs the real handler in-process. */
function inProcessWorker() {
  const w: any = { onmessage: null, terminate() {} };
  const handle = createHandler((msg) => queueMicrotask(() => w.onmessage?.({ data: msg })),
    { loadIndex: async () => index, open: openFixture, tilePx: J.tile_px });
  w.postMessage = (msg: unknown) => queueMicrotask(() => handle(msg as any));
  return w as Worker;
}

const spec = { bbox: J.bbox, yearA: 2019, yearB: 2024, resM: 10 };

test("events arrive as plan, tiles in order, then end", async () => {
  const ev: ClientEvent[] = [];
  const job = startJob(spec, (e) => ev.push(e), inProcessWorker);
  await job.done;
  expect(ev[0].type).toBe("plan");
  const tiles = ev.filter((e) => e.type === "tile") as any[];
  expect(tiles.map((t) => t.done)).toEqual(tiles.map((_, i) => i + 1));
  expect(ev.at(-1)).toMatchObject({ type: "end", cancelled: false, failed: 0 });
});

test("cancel ends the run marked cancelled", async () => {
  const ev: ClientEvent[] = [];
  const job = startJob(spec, (e) => { ev.push(e); if (e.type === "tile") job.cancel(); }, inProcessWorker);
  await job.done;
  expect(ev.at(-1)).toMatchObject({ type: "end", cancelled: true });
  expect(ev.filter((e) => e.type === "tile").length).toBeLessThan(J.runs["10"].tiles);
});

test("a failure to load the index is reported, not thrown", async () => {
  const ev: ClientEvent[] = [];
  const broken = () => {
    const w: any = { onmessage: null, terminate() {} };
    const handle = createHandler((m) => queueMicrotask(() => w.onmessage?.({ data: m })),
      { loadIndex: async () => { throw new Error("offline"); }, open: openFixture });
    w.postMessage = (m: unknown) => queueMicrotask(() => handle(m as any));
    return w as Worker;
  };
  await startJob(spec, (e) => ev.push(e), broken).done;
  expect(ev).toEqual([{ type: "error", message: expect.stringContaining("offline") }]);
});

test("tile reads skip the browser's HTTP cache (it tripled a tile's read time)", async () => {
  const { openNoStore } = await import("../src/workerlogic");
  const seen: RequestInit[] = [];
  const real = globalThis.fetch;
  globalThis.fetch = (async (_u: unknown, init: RequestInit) => { seen.push(init); throw new Error("offline"); }) as any;
  try {
    await expect(openNoStore("https://example.com/x.tif")).rejects.toThrow();
  } finally {
    globalThis.fetch = real;
  }
  expect(seen.length).toBeGreaterThan(0);
  expect(seen.every((i) => i.cache === "no-store")).toBe(true);
});

test("cancel stops at once: the worker is shut down and tiles it had already sent are dropped", () => {
  let onmessage: ((e: { data: unknown }) => void) | null = null;
  let terminated = false;
  const w: any = { set onmessage(f: any) { onmessage = f; }, get onmessage() { return onmessage; }, postMessage() {}, terminate: () => { terminated = true; } };
  const ev: ClientEvent[] = [];
  const hist = (v: number) => Float64Array.from({ length: 256 }, () => v);
  const job = startJob(spec, (e) => ev.push(e), () => w);
  onmessage!({ data: { type: "tile", done: 1, total: 9, result: { hist: hist(1) } } });
  job.cancel();
  expect(terminated).toBe(true);
  expect(ev.at(-1)).toMatchObject({ type: "end", cancelled: true });
  expect(Array.from((ev.at(-1) as any).histTotal.slice(0, 2))).toEqual([1, 1]);   // the tiles that did land
  onmessage!({ data: { type: "tile", done: 2, total: 9, result: { hist: hist(1) } } });   // already queued
  expect(ev.filter((e) => e.type === "tile")).toHaveLength(1);
  job.cancel();                                          // a second click does nothing more
  expect(ev.filter((e) => e.type === "end")).toHaveLength(1);
});

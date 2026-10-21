import { runJob, mosaic, type JobEvent } from "../src/engine/job";
import { TileIndex } from "../src/engine/tileindex";
import { f32, fxJson, u8, fixtureRow, openFixture } from "./fixtures";

const J = fxJson("job.json");

/** A TileIndex stand-in over the two fixture COGs (the real class's query logic is tested in tileindex.test). */
function fakeIndex(rows = [fixtureRow("cog_a.tif", 2019), fixtureRow("cog_b.tif", 2024)]): TileIndex {
  return {
    covering: (_b: unknown, y: number) => rows.filter((r) => r.year === y),
    rowFor: (_t: unknown, y: number) => rows.find((r) => r.year === y) ?? null,
  } as unknown as TileIndex;
}

async function collect(gen: AsyncGenerator<JobEvent>) {
  const ev: JobEvent[] = [];
  for await (const e of gen) ev.push(e);
  return ev;
}

test("the job's mosaic matches Python at 10 m and 50 m", async () => {
  for (const res of ["10", "50"]) {
    const ev = await collect(runJob({ bbox: J.bbox, yearA: 2019, yearB: 2024, resM: Number(res) },
      { index: fakeIndex(), open: openFixture, tilePx: J.tile_px }));
    const plan = ev[0] as Extract<JobEvent, { type: "plan" }>;
    const g = plan.grid;
    expect([g.crs, g.x0, g.y0, g.res, g.width, g.height]).toEqual(J.runs[res].grid);
    const tiles = ev.filter((e) => e.type === "tile").map((e) => (e as any).result);
    expect(tiles.length).toBe(J.runs[res].tiles);
    const m = mosaic(g, tiles);
    const ws = f32(J.runs[res].score.file), wc = u8(J.runs[res].cov.file);
    for (let i = 0; i < ws.length; i++) {
      if (ws[i] === -1 || m.score[i] === -1) expect(m.score[i]).toBe(ws[i]);
      else expect(Math.abs(m.score[i] - ws[i])).toBeLessThan(1e-5);
    }
    expect(Array.from(m.cov)).toEqual(Array.from(wc));
    const end = ev.at(-1) as Extract<JobEvent, { type: "end" }>;
    expect(Array.from(end.histTotal)).toEqual(J.runs[res].hist);
    expect([end.failed, end.cancelled]).toEqual([0, false]);
  }
});

test("a missing year B gives coverage 'no data in B' and no score", async () => {
  const ev = await collect(runJob({ bbox: J.bbox, yearA: 2019, yearB: 2024, resM: 50 },
    { index: fakeIndex([fixtureRow("cog_a.tif", 2019)]), open: openFixture, tilePx: J.tile_px }));
  const plan = ev[0] as any;
  const m = mosaic(plan.grid, ev.filter((e) => e.type === "tile").map((e: any) => e.result));
  expect(m.score.every((v) => v === -1)).toBe(true);
  expect(new Set(m.cov)).toEqual(new Set([3, 4]));
});

test("a tile that fails to read is counted and the run carries on", async () => {
  let calls = 0;
  const flaky = async (url: string) => {
    if (url === "cog_b.tif" && calls++ === 0) throw new Error("network down");
    return openFixture(url);
  };
  const ev = await collect(runJob({ bbox: J.bbox, yearA: 2019, yearB: 2024, resM: 10 },
    { index: fakeIndex(), open: flaky, tilePx: J.tile_px }));
  const end = ev.at(-1) as any;
  expect(end.failed).toBe(1);
  expect(ev.filter((e) => e.type === "tile").length).toBe(J.runs["10"].tiles);
});

test("cancelling after the first tile ends the run, marked cancelled", async () => {
  const ac = new AbortController();
  const ev: JobEvent[] = [];
  for await (const e of runJob({ bbox: J.bbox, yearA: 2019, yearB: 2024, resM: 10 },
    { index: fakeIndex(), open: openFixture, tilePx: J.tile_px }, ac.signal)) {
    ev.push(e);
    if (e.type === "tile") ac.abort();
  }
  expect(ev.filter((e) => e.type === "tile").length).toBe(1);
  expect((ev.at(-1) as any).cancelled).toBe(true);
});

test("a tile whose two years both fail counts once", async () => {
  const down = async () => { throw new Error("network down"); };
  const ev = await collect(runJob({ bbox: J.bbox, yearA: 2019, yearB: 2024, resM: 50 },
    { index: fakeIndex(), open: down, tilePx: J.tile_px }));
  expect((ev.at(-1) as any).failed).toBe(J.runs["50"].tiles);
});

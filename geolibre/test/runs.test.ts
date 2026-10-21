import "fake-indexeddb/auto";
import { openRunStore } from "../src/storage";
import { saveRunMeta, saveTile, loadRun, listRunRecords, runName, type RunRecord } from "../src/runs";
import type { TileResult } from "../src/engine/job";

const grid = { crs: "EPSG:32610", x0: 0, y0: 100, res: 10, width: 4, height: 2 };
const tile = (k: number): TileResult => ({
  window: [0, 2, k, k + 2], score: Float32Array.from([0.1, 0.2, -1, 0.9]), cov: Uint8Array.from([1, 1, 3, 1]),
  hist: Float64Array.from({ length: 256 }, (_, i) => (i === k ? 3 : 0)),
});
const meta = (id: string, complete: boolean): RunRecord => ({
  id, name: runName(2019, 2024, 10, new Date(2026, 9, 21)), createdAt: 1, bytes: 0,
  bbox: [-125.3, 49.6, -125.2, 49.7], yearA: 2019, yearB: 2024, resM: 10, grid,
  histTotal: [], complete, failed: 0, tiles: 0,
});

test("a run and its tiles round-trip", async () => {
  const s = await openRunStore("runs1");
  await saveRunMeta(s, meta("r1", true));
  await saveTile(s, "r1", 0, tile(0));
  await saveTile(s, "r1", 1, tile(2));
  const got = await loadRun(s, "r1");
  expect(got.meta.name).toBe("2019 to 2024, 10 m, 21 Oct 2026");
  expect(got.tiles.map((t) => t.window)).toEqual([[0, 2, 0, 2], [0, 2, 2, 4]]);
  expect(Array.from(got.tiles[1].score)).toEqual(Array.from(tile(2).score));
  expect(Array.from(got.tiles[1].cov)).toEqual([1, 1, 3, 1]);
  expect(got.tiles[1].hist[2]).toBe(3);
});

test("a cancelled run reloads its finished tiles and stays incomplete", async () => {
  const s = await openRunStore("runs2");
  await saveRunMeta(s, meta("r2", false));
  await saveTile(s, "r2", 0, tile(0));
  const got = await loadRun(s, "r2");
  expect(got.meta.complete).toBe(false);
  expect(got.tiles.length).toBe(1);
  expect((await listRunRecords(s)).map((r) => r.id)).toEqual(["r2"]);
});

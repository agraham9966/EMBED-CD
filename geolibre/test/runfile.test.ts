import { fromArrayBuffer } from "geotiff";
import { writeRunFile, readRunFile, runFileName } from "../src/runfile";
import { mosaic, type TileResult } from "../src/engine/job";
import { readZip } from "../src/engine/npz";
import type { RunRecord } from "../src/runs";

const grid = { crs: "EPSG:32610", x0: 500000, y0: 5500000, res: 10, width: 5, height: 3 };
const tiles: TileResult[] = [
  { window: [0, 3, 0, 3], score: Float32Array.from([0.1, 0.2, 0.3, -1, 0.5, 0.6, 0.7, 0.8, 0.9]),
    cov: Uint8Array.from([1, 1, 1, 2, 1, 1, 1, 1, 1]), hist: new Float64Array(256).fill(1) },
  { window: [0, 3, 3, 5], score: Float32Array.from([0.4, -1, 0.2, 0.3, -1, -1]),
    cov: Uint8Array.from([1, 3, 1, 1, 0, 4]), hist: new Float64Array(256).fill(2) },
];
const meta: RunRecord = { id: "r1", name: "2019 to 2024, 10 m, 21 Oct 2026", createdAt: 1, bbox: [-123, 49.6, -122.9, 49.7],
  yearA: 2019, yearB: 2024, resM: 10, grid, histTotal: Array(256).fill(3), complete: true, failed: 0, tiles: 2 };

test("a run file round-trips to the same map", async () => {
  const back = await readRunFile(await (await writeRunFile(meta, tiles)).arrayBuffer());
  expect(back.meta).toEqual(meta);
  const a = mosaic(grid, tiles), b = mosaic(back.meta.grid, back.tiles);
  expect(Array.from(b.score)).toEqual(Array.from(a.score));
  expect(Array.from(b.cov)).toEqual(Array.from(a.cov));
});

test("the change map inside opens as a georeferenced GeoTIFF with nodata -1", async () => {
  const files = await readZip(await (await writeRunFile(meta, tiles)).arrayBuffer());
  expect(Object.keys(files).sort()).toEqual(["change.tif", "coverage.tif", "run.json"]);
  const img = await (await fromArrayBuffer(files["change.tif"].slice().buffer)).getImage();
  expect([img.getWidth(), img.getHeight()]).toEqual([5, 3]);
  expect(img.getGeoKeys()?.ProjectedCSTypeGeoKey).toBe(32610);
  expect(img.getOrigin().slice(0, 2)).toEqual([500000, 5500000]);
  expect(img.getResolution().slice(0, 2)).toEqual([10, -10]);
  expect(img.getGDALNoData()).toBe(-1);
});

test("big maps reopen as display-sized tiles", async () => {
  const g = { ...grid, width: 2500, height: 1100 };
  const t: TileResult = { window: [0, 1100, 0, 2500], score: new Float32Array(2500 * 1100).fill(0.3),
    cov: new Uint8Array(2500 * 1100).fill(1), hist: new Float64Array(256) };
  const back = await readRunFile(await (await writeRunFile({ ...meta, grid: g }, [t])).arrayBuffer());
  expect(back.tiles.length).toBe(6);                 // 3 x 2 blocks of at most 1024 px
  expect(back.tiles.every((x) => x.window[1] - x.window[0] <= 1024 && x.window[3] - x.window[2] <= 1024)).toBe(true);
});

test("the file name says what the run is", () => {
  expect(runFileName(meta, new Date(2026, 9, 21))).toBe("embed-cd_2019-2024_10m_2026-10-21.zip");
});

import { fromArrayBuffer } from "geotiff";
import { factorFor, fetchTile } from "../src/engine/source";
import type { IndexRow } from "../src/engine/tileindex";
import { fxBuffer, fxJson, i8 } from "./fixtures";

const F = fxJson("fetch.json");
export const fixtureRow = (file: string, year: number): IndexRow => ({
  url: file, year, crs: "EPSG:32610",
  utmWest: F.cog_origin[0], utmSouth: F.cog_origin[1],
  utmEast: F.cog_origin[0] + F.npx * 10, utmNorth: F.cog_origin[1] + F.npx * 10,
  wgs84West: 0, wgs84South: 0, wgs84East: 0, wgs84North: 0,
});
export const openFixture = async (url: string) => fromArrayBuffer(fxBuffer(url));

test("factorFor never upsamples and stops at 16", () => {
  expect([10, 20, 50, 75, 100, 1000].map(factorFor)).toEqual([1, 2, 4, 4, 8, 16]);
});

test("fetchTile reads the same north-up cube as Python at factor 1 and 4", async () => {
  for (const f of ["1", "4"]) {
    const want = F.fetch[f];
    const [crs, west, south, east, north] = want.tile;
    for (const [side, file, year] of [["a", "cog_a.tif", 2019], ["b", "cog_b.tif", 2024]] as const) {
      const got = (await fetchTile(fixtureRow(file, year), { crs, west, south, east, north }, Number(f), openFixture))!;
      expect([got.cube.height, got.cube.width, 64]).toEqual(want[side].shape);
      expect(got.crs).toBe(want.crs);
      expect([got.transform.x0, got.transform.y0, got.transform.res]).toEqual(want.transform);
      expect(Buffer.from(got.cube.data.buffer).equals(Buffer.from(i8(want[side].file).buffer))).toBe(true);
    }
  }
});

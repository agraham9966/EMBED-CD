import { makeGrid, windowForBounds, estimate } from "../src/engine/grid";
import { TileIndex, listTiles } from "../src/engine/tileindex";
import { fxBuffer, fxJson } from "./fixtures";

const T = fxJson("tiles.json");
const FACTOR: Record<string, number> = { 10: 1, 50: 4, 75: 4, 100: 8 };

test("makeGrid and windowForBounds match Python", async () => {
  const idx = await TileIndex.load(async () => fxBuffer("index_sample.npz"));
  for (const name of ["vanisle", "zoneline"]) {
    for (const res of [10, 50, 75, 100]) {
      const want = T[name].grids[res];
      const g = makeGrid(T[name].bbox, "EPSG:32610", res);
      expect([g.crs, g.x0, g.y0, g.res, g.width, g.height]).toEqual(want.grid);
      const tiles = listTiles(idx, T[name].bbox, 2019, 2024, FACTOR[res]).all;
      const wins = tiles.map((t) => windowForBounds(g, [t.west, t.south, t.east, t.north], t.crs));
      expect(wins).toEqual(want.windows);
    }
  }
});

test("estimate warns past 60 million pixels", () => {
  const g = { crs: "EPSG:32610", x0: 0, y0: 0, res: 10, width: 10000, height: 6000 };
  expect(estimate(g, 3).tooBig).toBe(false);
  expect(estimate({ ...g, height: 6001 }, 3).tooBig).toBe(true);
  expect(estimate(g, 3).gb).toBeCloseTo(3 * 2 * 67e6 / 1e9, 12);
});

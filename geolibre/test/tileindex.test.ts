import { readNpz, npyString } from "../src/engine/npz";
import { TileIndex, listTiles, type Tile } from "../src/engine/tileindex";
import { transformBounds, utmCrs } from "../src/engine/crs";
import { fxBuffer, fxJson } from "./fixtures";

const T = fxJson("tiles.json");
const loadSample = () => TileIndex.load(async () => fxBuffer("index_sample.npz"));

test("readNpz reads the index columns", async () => {
  const z = await readNpz(fxBuffer("index_sample.npz"));
  expect(Object.keys(z).sort()).toEqual(
    ["crs", "key", "utm_east", "utm_north", "utm_south", "utm_west",
     "wgs84_east", "wgs84_north", "wgs84_south", "wgs84_west", "year"]);
  expect(z.year.shape).toEqual([6]);
  expect(npyString(z.crs, 0)).toMatch(/^EPSG:326\d\d$/);
});

test("covering matches Python for both boxes", async () => {
  const idx = await loadSample();
  for (const name of ["vanisle", "zoneline"]) {
    for (const y of [2019, 2024]) {
      expect(idx.covering(T[name].bbox, y).map((r) => r.url).sort()).toEqual(T[name].covering[y]);
    }
  }
});

test("listTiles matches Python for factors 1, 4 and 8", async () => {
  const idx = await loadSample();
  const asList = (ts: Tile[]) => ts.map((t) => [t.crs, t.west, t.south, t.east, t.north]);
  for (const name of ["vanisle", "zoneline"]) {
    for (const f of [1, 4, 8]) {
      const got = listTiles(idx, T[name].bbox, 2019, 2024, f);
      const want = T[name].tiles[f];
      for (const k of ["all", "both", "partial"] as const) {
        const g = asList(got[k]), w = want[k];
        expect(g.map((t) => t[0])).toEqual(w.map((t: any[]) => t[0]));
        g.forEach((t, i) => t.slice(1).forEach((v, j) => expect(v).toBeCloseTo(w[i][j + 1], 6)));
      }
    }
  }
});

test("transformBounds matches GDAL's densified bounds", () => {
  for (const name of ["vanisle", "zoneline"]) {
    for (const [key, crs] of [["4326_to_32610", "EPSG:32610"], ["4326_to_32609", "EPSG:32609"]]) {
      const got = transformBounds("EPSG:4326", crs, T[name].bbox);
      got.forEach((v, i) => expect(Math.abs(v - T[name].bounds[key][i])).toBeLessThan(0.01));
    }
  }
});

test("utmCrs picks the zone of the centre, Web Mercator near the poles", () => {
  expect(utmCrs([-125.34, 49.63, -125.24, 49.71])).toBe("EPSG:32610");
  expect(utmCrs([-126.1, 49.6, -125.9, 49.7])).toBe("EPSG:32610");
  expect(utmCrs([10, -30, 11, -29])).toBe("EPSG:32732");
  expect(utmCrs([10, 85, 11, 86])).toBe("EPSG:3857");
});

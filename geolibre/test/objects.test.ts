import { polygonize, type ObjectPoly } from "../src/engine/objects";
import { f32, fxJson } from "./fixtures";

const J = fxJson("job.json");
const gridOf = (g: any[]) => ({ crs: g[0], x0: g[1], y0: g[2], res: g[3], width: g[4], height: g[5] });

/** Python WKT polygon -> rings of [x, y]. */
function wktRings(wkt: string): [number, number][][] {
  return [...wkt.matchAll(/\(([^()]+)\)/g)].map((m) => m[1].split(",").map((p) => p.trim().split(" ").map(Number) as [number, number]));
}
/** The multiset of a polygon's ring vertices (the closing repeat dropped), as sorted strings. */
const vertices = (rings: [number, number][][]) =>
  rings.flatMap((r) => r.slice(0, -1).map(([x, y]) => `${x},${y}`)).sort();

function sameObjects(got: ObjectPoly[], want: any[], res: number) {
  expect(got.length).toBe(want.length);
  for (const w of want) {
    const g = got.find((o) => o.nPx === w.n_px && Math.abs(o.chgMean - w.chg_mean) < 1e-6);
    expect(g, `object with ${w.n_px} px`).toBeTruthy();
    expect(g!.areaHa).toBeCloseTo(w.area_ha, 9);
    expect(Math.abs(g!.chgMax - w.chg_max)).toBeLessThan(1e-6);
    expect(g!.rings.length).toBe(wktRings(w.wkt).length);
    expect(vertices(g!.rings)).toEqual(vertices(wktRings(w.wkt)));
    let area = 0;                                      // outer minus holes = the pixels exactly
    g!.rings.forEach((r, i) => {
      let a = 0;
      for (let k = 0; k < r.length - 1; k++) a += r[k][0] * r[k + 1][1] - r[k + 1][0] * r[k][1];
      area += (i === 0 ? 1 : -1) * Math.abs(a / 2);
    });
    expect(area).toBeCloseTo(w.n_px * res * res, 3);
  }
}

test("objects from the job's change map match GDAL's at 10 m and 50 m", async () => {
  for (const res of ["10", "50"]) {
    const o = J.runs[res].objects;
    const got = await polygonize(gridOf(J.runs[res].grid), f32(J.runs[res].score.file), o.cutoff, o.min_area);
    sameObjects(got, o.polys, Number(res));
  }
});

test("rings, holes, diagonal joins, single pixels and edges match GDAL", async () => {
  const s = J.runs["10"].synthetic;
  const got = await polygonize(gridOf(J.runs["10"].grid), f32(s.score.file), s.cutoff, s.min_area);
  sameObjects(got, s.polys, 10);
});

test("objects come in GDAL's order: by bottom row, then by first pixel", async () => {
  const s = J.runs["10"].synthetic;
  const got = await polygonize(gridOf(J.runs["10"].grid), f32(s.score.file), s.cutoff, s.min_area);
  expect(got.map((o) => [o.nPx, Math.round(o.chgMean * 1e6)])).toEqual(
    s.polys.map((p: any) => [p.n_px, Math.round(p.chg_mean * 1e6)]));
});

import { attachVectors } from "../src/engine/objects";
import { runJob } from "../src/engine/job";
import { TileIndex } from "../src/engine/tileindex";
import { fixtureRow, openFixture } from "./fixtures";

async function jobCells(res: number) {
  const rows = [fixtureRow("cog_a.tif", 2019), fixtureRow("cog_b.tif", 2024)];
  const index = { covering: (_b: unknown, y: number) => rows.filter((r) => r.year === y),
    rowFor: (_t: unknown, y: number) => rows.find((r) => r.year === y) ?? null } as unknown as TileIndex;
  const cells = [];
  for await (const e of runJob({ bbox: J.bbox, yearA: 2019, yearB: 2024, resM: res }, { index, open: openFixture, tilePx: J.tile_px })) {
    if (e.type === "tile" && e.result.cells) cells.push(e.result.cells);
  }
  return cells;
}

function sameVectors(got: Float32Array, file: string) {
  const want = f32(file);
  expect(got.length).toBe(want.length);
  let worst = 0;
  for (let i = 0; i < want.length; i++) worst = Math.max(worst, Math.abs(got[i] - want[i]));
  expect(worst).toBeLessThan(1e-5);
}

test("object vectors match Python's attach_vectors at 10 m and 50 m", async () => {
  for (const res of ["10", "50"]) {
    const o = J.runs[res].objects;
    const polys = await polygonize(gridOf(J.runs[res].grid), f32(J.runs[res].score.file), o.cutoff, o.min_area);
    sameVectors(await attachVectors(polys, "EPSG:32610", await jobCells(Number(res))), o.vectors.file);
  }
});

test("vectors for 37 awkward objects, including single pixels and neighbours sharing cells, match", async () => {
  const s = J.runs["10"].synthetic;
  const polys = await polygonize(gridOf(J.runs["10"].grid), f32(s.score.file), s.cutoff, s.min_area);
  const v = await attachVectors(polys, "EPSG:32610", await jobCells(10));
  sameVectors(v, s.vectors.file);
  const single = polys.findIndex((p) => p.nPx === 1);
  expect(v.subarray(single * 128, single * 128 + 128).some((x) => x !== 0)).toBe(true);
});

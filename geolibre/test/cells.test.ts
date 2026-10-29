import { CELL_SCALE, poolCells } from "../src/engine/cells";
import { changeScore } from "../src/engine/score";
import type { Cube } from "../src/engine/source";
import { f32, fxJson, i8 } from "./fixtures";

const F = fxJson("fetch.json"), C = fxJson("cells.json");
const cube = (file: string, shape: number[]): Cube => ({ data: i8(file), height: shape[0], width: shape[1], bands: 64 });

test("poolCells matches Python's cells.pool", () => {
  const a = cube(F.fetch["1"].a.file, F.fetch["1"].a.shape), b = cube(F.fetch["1"].b.file, F.fetch["1"].b.shape);
  const { score, cov } = changeScore(a, b);
  const t = { x0: F.fetch["1"].transform[0], y0: F.fetch["1"].transform[1], res: 10 };
  const g = poolCells(a, b, score, cov, 16, t, "EPSG:32610");
  expect([g.height, g.width, g.res, g.x0, g.y0]).toEqual([8, 8, 160, t.x0, t.y0]);
  const close = (got: ArrayLike<number>, file: string, scale = 1, tol = 1e-6) => {
    const want = f32(file);
    expect(got.length).toBe(want.length);
    for (let i = 0; i < want.length; i++) expect(Math.abs(got[i] / scale - want[i])).toBeLessThan(tol);
  };
  // The means are kept in 16 bits: within half a step (1/65534) of Python's float32.
  const half = 0.5 / CELL_SCALE + 1e-6;
  close(g.ma, C.ma.file, CELL_SCALE, half); close(g.mb, C.mb.file, CELL_SCALE, half); close(g.n, C.n.file); close(g.smean, C.smean.file); close(g.smax, C.smax.file);
});

test("a kept window pools only those cells, with the same values", () => {
  const a = cube(F.fetch["1"].a.file, F.fetch["1"].a.shape), b = cube(F.fetch["1"].b.file, F.fetch["1"].b.shape);
  const { score, cov } = changeScore(a, b);
  const t = { x0: F.fetch["1"].transform[0], y0: F.fetch["1"].transform[1], res: 10 };
  const all = poolCells(a, b, score, cov, 16, t, "EPSG:32610");
  const part = poolCells(a, b, score, cov, 16, t, "EPSG:32610", { r0: 2, r1: 5, c0: 1, c1: 4 });
  expect([part.height, part.width, part.x0, part.y0]).toEqual([3, 3, t.x0 + 160, t.y0 - 320]);
  expect(part.n[0]).toBe(all.n[2 * 8 + 1]);
  expect(part.ma[64 * 4 + 7]).toBe(all.ma[64 * (3 * 8 + 2) + 7]);
});

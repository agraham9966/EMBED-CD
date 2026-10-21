import { changeScore, histogram, otsu, fractionAbove, NODATA } from "../src/engine/score";
import type { Cube } from "../src/engine/source";
import { f32, fxJson, i8, u8 } from "./fixtures";

const F = fxJson("fetch.json"), S = fxJson("score.json");
const cube = (file: string, shape: number[]): Cube =>
  ({ data: i8(file), height: shape[0], width: shape[1], bands: 64 });

test("changeScore, histogram, otsu and fractionAbove match Python", () => {
  const a = cube(F.fetch["1"].a.file, F.fetch["1"].a.shape);
  const b = cube(F.fetch["1"].b.file, F.fetch["1"].b.shape);
  const { score, cov } = changeScore(a, b);
  const want = f32("score.bin");
  let worst = 0;
  for (let i = 0; i < want.length; i++) {
    if (want[i] === NODATA) expect(score[i]).toBe(NODATA);
    else worst = Math.max(worst, Math.abs(score[i] - want[i]));
  }
  expect(worst).toBeLessThan(1e-6);
  expect(Buffer.from(cov).equals(Buffer.from(u8("cov.bin")))).toBe(true);
  const hist = histogram(score);
  expect(Array.from(hist)).toEqual(S.hist);
  expect(otsu(hist)).toBeCloseTo(S.otsu, 12);
  expect(fractionAbove(hist, 0.3)).toBeCloseTo(S["fraction_above_0.3"], 12);
});

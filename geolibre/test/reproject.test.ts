import { reprojectInto } from "../src/engine/reproject";
import { f32, fxJson, u8 } from "./fixtures";

const R = fxJson("reproject.json");
const [h, w] = R.src_shape;
const srcT = { x0: R.src_transform[0], y0: R.src_transform[1], res: R.src_transform[2] };
const score = f32("score.bin");
const cov = Float32Array.from(u8("cov.bin"));

function run(name: string, src: Float32Array, nodata: number) {
  const c = R.cases[name];
  const dstT = { x0: c.dst_transform[0], y0: c.dst_transform[1], res: c.dst_transform[2] };
  const got = reprojectInto(src, [h, w], "EPSG:32610", srcT, c.dst_crs, dstT, c.out.shape, nodata, c.mode);
  return { got, want: f32(c.out.file) };
}

test("same-CRS average to 50 m and 75 m matches GDAL on every pixel", () => {
  for (const name of ["same_50", "same_75"]) {
    const { got, want } = run(name, score, -1);
    for (let i = 0; i < want.length; i++) {
      if (want[i] === -1 || got[i] === -1) expect(got[i]).toBe(want[i]);
      else expect(Math.abs(got[i] - want[i])).toBeLessThan(1e-5);
    }
  }
});

test("cross-zone average and nearest match GDAL on every pixel", () => {
  const s = run("zone_score", score, -1);
  for (let i = 0; i < s.want.length; i++) {
    if (s.want[i] === -1 || s.got[i] === -1) expect(s.got[i]).toBe(s.want[i]);
    else expect(Math.abs(s.got[i] - s.want[i])).toBeLessThan(1e-5);
  }
  const c = run("zone_cov", cov, 0);
  expect(Array.from(c.got)).toEqual(Array.from(c.want));
});

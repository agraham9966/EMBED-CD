// Warp one band into another grid, standing in for GDAL ReprojectImage (embed_cd/gdalio.py).
// "average" = area-weighted mean of the valid source pixels under each destination pixel
// (GDAL's GRA_Average); "nearest" = the source pixel under the destination pixel's centre.
import { transformer } from "./crs";
import type { Affine } from "./source";

export function reprojectInto(
  src: Float32Array,
  [sh, sw]: [number, number],
  srcCrs: string,
  srcT: Affine,
  dstCrs: string,
  dstT: Affine,
  [dh, dw]: [number, number],
  nodata: number,
  mode: "average" | "nearest",
): Float32Array {
  const out = new Float32Array(dh * dw).fill(nodata);
  // Source pixel coordinates of every destination pixel CORNER, computed once and shared by the
  // four pixels that meet there: (dw+1) x (dh+1) transforms rather than four per pixel.
  const W = dw + 1;
  const sx = new Float64Array(W * (dh + 1)), sy = new Float64Array(W * (dh + 1));
  const same = srcCrs === dstCrs;
  const f = same ? null : transformer(dstCrs, srcCrs);
  for (let r = 0; r <= dh; r++) {
    for (let c = 0; c <= dw; c++) {
      let X = dstT.x0 + c * dstT.res, Y = dstT.y0 - r * dstT.res;
      if (f) [X, Y] = f(X, Y);
      sx[r * W + c] = (X - srcT.x0) / srcT.res;
      sy[r * W + c] = (srcT.y0 - Y) / srcT.res;
    }
  }
  for (let r = 0; r < dh; r++) {
    for (let c = 0; c < dw; c++) {
      const i00 = r * W + c, i01 = i00 + 1, i10 = i00 + W, i11 = i10 + 1;
      if (mode === "nearest") {
        const x = (sx[i00] + sx[i01] + sx[i10] + sx[i11]) / 4;
        const y = (sy[i00] + sy[i01] + sy[i10] + sy[i11]) / 4;
        const px = Math.floor(x), py = Math.floor(y);
        if (px >= 0 && py >= 0 && px < sw && py < sh) {
          const v = src[py * sw + px];
          if (v !== nodata) out[r * dw + c] = v;
        }
        continue;
      }
      // GDAL's average builds the source window from the destination pixel's top-left and
      // bottom-right corners only (measured: this matches GDAL far better than the box of all
      // four corners or the exact rotated footprint).
      const x0 = Math.min(sx[i00], sx[i11]), x1 = Math.max(sx[i00], sx[i11]);
      const y0 = Math.min(sy[i00], sy[i11]), y1 = Math.max(sy[i00], sy[i11]);
      let sum = 0, wsum = 0;
      for (let py = Math.max(0, Math.floor(y0)); py < Math.min(sh, Math.ceil(y1)); py++) {
        for (let px = Math.max(0, Math.floor(x0)); px < Math.min(sw, Math.ceil(x1)); px++) {
          const v = src[py * sw + px];
          if (v === nodata) continue;
          const wgt = (Math.min(py + 1, y1) - Math.max(py, y0)) * (Math.min(px + 1, x1) - Math.max(px, x0));
          if (wgt <= 0) continue;
          sum += v * wgt;
          wsum += wgt;
        }
      }
      if (wsum > 0) out[r * dw + c] = sum / wsum;
    }
  }
  return out;
}


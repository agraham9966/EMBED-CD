"""Turn the change raster into objects, and give each one the embedding of what it covers.

This is the half of the design that runs AFTER the job, on files already on disk — which is what
makes the threshold free. Re-cutting at a different cutoff costs seconds and no downloads, and
that matters because objects are emphatically NOT nested across thresholds: measured, one object
at 0.015 broke into 125 separate objects at 0.05. There is no "capture once, filter later" for
geometry, so geometry is simply recomputed whenever it is asked for.

Two rules earn their keep:

* **Vectors are transformed, cell grids never are.** Cells live in the tile's native UTM; polygons
  come off the raster in the project's CRS. We move the polygon into UTM to look cells up.
  Reprojecting the cell grids instead would resample aggregates and quietly corrupt the totals.
* **Count-weighted means.** A polygon spanning tiles combines cells as
  `sum(mean_i * n_i) / sum(n_i)` — exactly the mean over its pixels. An unweighted average of
  cell means would over-weight partial edge cells.

numpy + scipy + GDAL only. Writing the GeoPackage is the plugin's job, using QGIS's own
vector API — the engine never imports QGIS.
"""
import os
import struct

import numpy as np

from . import cells as CE
from . import score as S

_M2_PER_HA = 10000.0
_CONNECTIVITY = 8            # matches the 3x3 structure used when the thresholds were measured


def polygonize(vrt_path, threshold, min_area_ha=1.0):
    """[{wkt, area_ha, chg_mean, chg_max, n_px}] for connected change at or above `threshold`.

    Components are labelled first and their statistics taken from the pixels directly, so area
    and mean are exact rather than derived from the traced outline.
    """
    from scipy import ndimage
    from . import gdalio as GD

    arr, crs, transform = GD.read(vrt_path, band=1)
    px_ha = abs(transform.a * transform.e) / _M2_PER_HA

    mask = np.isfinite(arr) & (arr != S.NODATA) & (arr >= threshold)
    if not mask.any():
        return [], crs
    labels, n = ndimage.label(mask, structure=np.ones((3, 3)))
    if n == 0:
        return [], crs

    idx = np.arange(1, n + 1)
    counts = np.bincount(labels.ravel(), minlength=n + 1)[1:]
    means = ndimage.mean(arr, labels, idx)
    maxes = ndimage.maximum(arr, labels, idx)
    keep = counts * px_ha >= min_area_ha
    if not keep.any():
        return [], crs

    wanted = set(int(v) for v in idx[keep])
    out = []
    for wkt, value in GD.polygonize(labels, mask, crs, transform, _CONNECTIVITY):
        if value not in wanted:
            continue
        out.append({"wkt": wkt,
                    "area_ha": float(counts[value - 1] * px_ha),
                    "chg_mean": float(means[value - 1]),
                    "chg_max": float(maxes[value - 1]),
                    "n_px": int(counts[value - 1])})
        wanted.discard(value)                 # one geometry per component
    return out, crs


class CellIndex:
    """The cell stores of one job, opened once and reused across every polygon."""

    def __init__(self, out_dir, year_a, year_b, cell_px=CE.CELL_PX):
        prefix = f"cells_{year_a}-{year_b}_{cell_px}px_"
        self.files = sorted(os.path.join(out_dir, f) for f in os.listdir(out_dir)
                            if f.startswith(prefix) and f.endswith(".tif")) if \
            os.path.isdir(out_dir) else []
        self._loaded = None

    def __bool__(self):
        return bool(self.files)

    def load(self):
        if self._loaded is None:
            self._loaded = [CE.read_cells(p) for p in self.files]
        return self._loaded

    @property
    def depth(self):
        return self.load()[0][0].shape[2] if self.files else 0


def attach_vectors(polygons, index, src_crs, pool_cutoff=None, progress=None):
    """[N, 2*depth] float32 — each polygon's count-weighted mean embedding, year A then year B.

    `pool_cutoff` skips cells whose own mean change score is below it, trading context for
    purity. `None` uses every covered cell. A polygon the cutoff empties keeps its unfiltered
    vector rather than coming back as zeros.
    """
    from . import gdalio as GD

    grids = index.load()
    depth = index.depth
    out = np.zeros((len(polygons), 2 * depth), np.float32)
    if not grids or not polygons:
        return out

    acc = np.zeros((len(polygons), 2 * depth), np.float64)
    acc_all = np.zeros((len(polygons), 2 * depth), np.float64)
    weight = np.zeros(len(polygons))
    weight_all = np.zeros(len(polygons))

    for gi, (ma, mb, n, smean, _smax, crs, tr) in enumerate(grids):
        # ONE rasterization per cell grid, burning each polygon's 1-based index, instead of one
        # per polygon per grid. Polygons come from connected components so they never overlap.
        wkts = [GD.transform_wkt(p["wkt"], src_crs, str(crs)) for p in polygons]
        burned = GD.rasterize_index(wkts, str(crs), ma.shape[:2], tr)
        present = np.unique(burned)
        for i, poly in enumerate(polygons):
            if progress is not None and progress(gi * len(polygons) + i,
                                                len(grids) * len(polygons)) is False:
                return out
            if (i + 1) not in present:
                continue
            hit = (burned == i + 1) & (n > 0)
            if not hit.any():
                continue
            w = n[hit]
            vecs = np.concatenate([ma[hit], mb[hit]], axis=1)
            acc_all[i] += (vecs * w[:, None]).sum(axis=0)
            weight_all[i] += w.sum()
            if pool_cutoff is not None:
                keep = smean[hit] >= pool_cutoff
                if not keep.any():
                    continue
                vecs, w = vecs[keep], w[keep]
            acc[i] += (vecs * w[:, None]).sum(axis=0)
            weight[i] += w.sum()

    for i in range(len(polygons)):
        if weight[i] > 0:
            out[i] = acc[i] / weight[i]
        elif weight_all[i] > 0:
            out[i] = acc_all[i] / weight_all[i]
    return out


def pack_vec(vec):
    """float32 vector -> bytes, for the GeoPackage BLOB column."""
    v = np.asarray(vec, np.float32)
    return struct.pack(f"<{v.size}f", *v.tolist())


def unpack_vec(blob):
    return np.frombuffer(bytes(blob), dtype="<f4").astype(np.float32)

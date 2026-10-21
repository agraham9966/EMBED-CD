"""Reference outputs from the Python engine, for the GeoLibre (TypeScript) port's parity tests.

Run with QGIS's Python (it has GDAL):
    "C:\\Program Files\\QGIS 4.0.1\\bin\\python-qgis.bat" scripts/make_geolibre_fixtures.py

Writes geolibre/test/fixtures/. Every array is little-endian, row-major, north-up; its shape and
dtype are recorded in the JSON next to it. The synthetic COGs are small (256 px, 128 px blocks)
and spatially smooth so they compress well; the engine's tile size is a parameter, so the port is
tested with TILE_PX = 128 and uses 1024 in production.
"""
import json
import os
import shutil
import sys
import tempfile

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from embed_cd import gdalio as GD, grid as G, job, score as S, source as SRC  # noqa: E402

OUT = os.path.join(ROOT, "geolibre", "test", "fixtures")
CRS = "EPSG:32610"
X0, Y0 = 500000.0, 5490000.0           # bottom-left corner of the synthetic COGs
NPX = 256
TILE = 128
BBOXES = {"vanisle": [-125.34, 49.63, -125.24, 49.71], "zoneline": [-126.10, 49.60, -125.90, 49.70]}


def put(name, arr):
    arr = np.ascontiguousarray(arr)
    arr.astype(arr.dtype.newbyteorder("<")).tofile(os.path.join(OUT, name))
    print("wrote", name, arr.shape, arr.dtype)
    return {"file": name, "shape": list(arr.shape), "dtype": str(arr.dtype)}


def dump(name, obj):
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=1)
    print("wrote", name)


def smooth_cube(rng):
    """[64, NPX, NPX] int8: blocky fields (16 px blocks), so ZSTD shrinks it to almost nothing."""
    coarse = rng.integers(-110, 110, (64, NPX // 16, NPX // 16))
    field = np.kron(coarse, np.ones((1, 16, 16), dtype=np.int64))
    return np.clip(field, -127, 127).astype(np.int8)


def write_cog(path, data):
    """South-up (positive y-res) int8 COG with overviews, like the real AlphaEarth files."""
    tmp = path + ".src.tif"
    GD.write(tmp, data, CRS, GD.Transform(10.0, 0.0, X0, 0.0, 10.0, Y0), nodata=-128)
    gdal = GD._gdal()
    ds = gdal.Open(tmp, 1)
    ds.BuildOverviews("NEAREST", [2, 4, 8])        # explicit: AUTO stops once a level fits a block
    ds = None
    gdal.Translate(path, tmp, format="COG",
                   creationOptions=["COMPRESS=ZSTD", "BLOCKSIZE=128", "OVERVIEWS=FORCE_USE_EXISTING"])
    os.remove(tmp)
    print("wrote", os.path.basename(path), os.path.getsize(path), "bytes")


class LocalIndex(SRC.Index):
    def __init__(self, base, rows):
        self.cache_dir = None
        self.base = base
        n = len(rows)
        self._d = {"key": np.array([r[0] for r in rows], dtype="S"),
                   "year": np.array([r[1] for r in rows], dtype=np.int32),
                   "crs": np.array([CRS] * n, dtype="S"),
                   "utm_west": np.full(n, X0), "utm_south": np.full(n, Y0),
                   "utm_east": np.full(n, X0 + NPX * 10.0), "utm_north": np.full(n, Y0 + NPX * 10.0),
                   **{k: np.full(n, v) for k, v in zip(
                       ("wgs84_west", "wgs84_south", "wgs84_east", "wgs84_north"),
                       GD.transform_bounds(CRS, "EPSG:4326", X0, Y0, X0 + NPX * 10.0,
                                           Y0 + NPX * 10.0))}}


def requantize(cube):
    """The int8 that de-quantizes to exactly this cube (all-zero vectors are nodata, -128).
    Stored instead of float32: four times smaller, and checked to round-trip bit for bit."""
    q = np.rint(np.sign(cube) * np.sqrt(np.abs(cube)) * 127.5).astype(np.int8)
    q[~(cube != 0).any(axis=-1)] = -128
    assert np.array_equal(SRC._dequantize(q), cube), "requantize does not round-trip"
    return q


def tile_list(tiles):
    return [[t.crs, t.west, t.south, t.east, t.north] for t in tiles]


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    # ---- the real index: a sample around both test boxes ---------------------------------
    real = SRC.Index(cache_dir=os.path.join(tempfile.gettempdir(), "embed_cd_fixture_index"))
    d = real.load()
    keep = np.zeros(len(d["year"]), bool)
    for bbox in BBOXES.values():
        w, s, e, n = bbox
        keep |= (np.isin(d["year"], [2019, 2024]) & (d["wgs84_west"] < e) & (d["wgs84_east"] > w)
                 & (d["wgs84_south"] < n) & (d["wgs84_north"] > s))
    sample = {k: v[keep] for k, v in d.items()}
    np.savez_compressed(os.path.join(OUT, "index_sample.npz"), **sample)
    print("wrote index_sample.npz rows", int(keep.sum()))

    sample_index = SRC.Index(cache_dir=None)
    sample_index._d = sample
    tiles = {}
    for name, bbox in BBOXES.items():
        utm = "EPSG:32610"                       # the centre of both boxes is in zone 10
        entry = {"bbox": bbox, "covering": {}, "tiles": {}, "grids": {}, "bounds": {}}
        for y in (2019, 2024):
            entry["covering"][y] = sorted(r["path"] for r in sample_index.covering(bbox, y))
        for f in (1, 4, 8):
            src = SRC.AlphaEarthSource(index=sample_index, factor=f)
            all_, both, partial = src.list_tiles(bbox, 2019, 2024)
            entry["tiles"][f] = {"all": tile_list(all_), "both": tile_list(both),
                                 "partial": tile_list(partial)}
        for res in (10, 50, 75, 100):
            g = G.make_grid(bbox, utm, res)
            src = SRC.AlphaEarthSource(index=sample_index, factor=SRC.factor_for(res))
            wins = []
            for t in src.list_tiles(bbox, 2019, 2024)[0]:
                wins.append(G.window_for_bounds(g, (t.west, t.south, t.east, t.north), t.crs))
            entry["grids"][res] = {"grid": list(g), "windows": wins}
        entry["bounds"]["4326_to_32610"] = list(GD.transform_bounds("EPSG:4326", "EPSG:32610", *bbox))
        entry["bounds"]["4326_to_32609"] = list(GD.transform_bounds("EPSG:4326", "EPSG:32609", *bbox))
        tiles[name] = entry
    dump("tiles.json", tiles)

    # ---- synthetic COGs -------------------------------------------------------------------
    rng = np.random.default_rng(7)
    a = smooth_cube(rng)
    # All in the south-west 128 px tile (rows count NORTHWARD in these south-up files), so the
    # fetch and score fixtures see a change, a year-A gap and a year-B gap together.
    b = a.copy()
    a[:, 100:115, 10:30] = -128                          # nodata in year A only
    b[:, 40:120, 60:160] = smooth_cube(rng)[:, 40:120, 60:160]   # the "change"
    b[:, 20:30, 100:120] = -128                          # nodata in year B
    write_cog(os.path.join(OUT, "cog_a.tif"), a)
    write_cog(os.path.join(OUT, "cog_b.tif"), b)

    base = OUT + os.sep
    idx = LocalIndex(base, [("cog_a.tif", 2019), ("cog_b.tif", 2024)])
    fetch_meta = {}
    cubes = {}
    for f in (1, 4):
        src = SRC.AlphaEarthSource(index=idx, tile_px=TILE, factor=f)
        # factor 1: the south-west block; factor 4: one tile covers the whole (clipped) file
        span = TILE * 10.0 * f
        t = SRC.Tile(CRS, X0, Y0, min(X0 + span, X0 + NPX * 10.0), min(Y0 + span, Y0 + NPX * 10.0))
        ca, crs, tr = src.fetch(t, 2019)
        cb, _, _ = src.fetch(t, 2024)
        cubes[f] = (ca, cb)
        fetch_meta[f] = {"tile": [t.crs, t.west, t.south, t.east, t.north], "crs": crs,
                         "transform": [tr.c, tr.f, tr.a],
                         "a": put(f"fetch_a_{f}.bin", requantize(ca)),
                         "b": put(f"fetch_b_{f}.bin", requantize(cb))}
    dump("fetch.json", {"tile_px": TILE, "cog_origin": [X0, Y0], "npx": NPX, "fetch": fetch_meta})

    # ---- score ----------------------------------------------------------------------------
    ca, cb = cubes[1]
    sc, cov = S.change_score(ca, cb)
    hist = S.histogram(sc)
    dump("score.json", {"score": put("score.bin", sc), "cov": put("cov.bin", cov),
                        "hist": hist.tolist(), "otsu": S.otsu_from_histogram(hist),
                        "fraction_above_0.3": S.fraction_above(hist, 0.3)})

    # ---- reprojection ---------------------------------------------------------------------
    tr = fetch_meta[1]["transform"]
    src_t = GD.Transform.from_origin(tr[0], tr[1], 10.0, 10.0)
    h, w = sc.shape
    rep = {"src_transform": [tr[0], tr[1], 10.0], "src_shape": [h, w], "cases": {}}
    for res in (50, 75):
        dt = GD.Transform.from_origin(tr[0], tr[1], float(res), float(res))
        shape = (int(np.ceil(h * 10 / res)), int(np.ceil(w * 10 / res)))
        out = GD.reproject_into(sc, CRS, src_t, CRS, dt, shape, S.NODATA, nearest=False)
        rep["cases"][f"same_{res}"] = {"dst_crs": CRS, "dst_transform": [tr[0], tr[1], float(res)],
                                       "mode": "average", "out": put(f"rep_same_{res}.bin", out)}
    left, bottom, right, top = GD.transform_bounds(CRS, "EPSG:32609", tr[0], tr[1] - h * 10.0,
                                                   tr[0] + w * 10.0, tr[1])
    x0 = np.floor(left / 10.0) * 10.0
    y0 = np.ceil(top / 10.0) * 10.0
    shape = (int(np.ceil((y0 - bottom) / 10.0)), int(np.ceil((right - x0) / 10.0)))
    dt = GD.Transform.from_origin(x0, y0, 10.0, 10.0)
    out_s = GD.reproject_into(sc, CRS, src_t, "EPSG:32609", dt, shape, S.NODATA, nearest=False)
    out_c = GD.reproject_into(cov.astype(np.float32), CRS, src_t, "EPSG:32609", dt, shape,
                              float(S.COV_NO_TILE), nearest=True)
    rep["cases"]["zone_score"] = {"dst_crs": "EPSG:32609", "dst_transform": [x0, y0, 10.0],
                                  "mode": "average", "out": put("rep_zone_score.bin", out_s)}
    rep["cases"]["zone_cov"] = {"dst_crs": "EPSG:32609", "dst_transform": [x0, y0, 10.0],
                                "mode": "nearest", "src": "cov", "out": put("rep_zone_cov.bin", out_c)}
    dump("reproject.json", rep)

    # ---- the whole job --------------------------------------------------------------------
    jobs = {}
    bbox = list(GD.transform_bounds(CRS, "EPSG:4326", X0 + 300, Y0 + 300,
                                    X0 + NPX * 10.0 - 300, Y0 + NPX * 10.0 - 300))
    for res in (10, 50):
        tmp = tempfile.mkdtemp(prefix="ecd_fixture_job_")
        src = SRC.AlphaEarthSource(index=idx, tile_px=TILE, factor=SRC.factor_for(res))
        g, records, hist_total, _ = job.run(tuple(bbox), 2019, 2024, tmp, dst_crs=CRS, res_m=res,
                                            src=src)
        from embed_cd import vrt
        vp = os.path.join(tmp, "m.vrt")
        vrt.write_vrt(vp, g, records)
        arr, _, _ = GD.read(vp, band=1)
        cv, _, _ = GD.read(vp, band=2)
        jobs[res] = {"grid": list(g), "hist": hist_total.tolist(), "tiles": len(records),
                     "score": put(f"job_score_{res}.bin", arr.astype(np.float32)),
                     "cov": put(f"job_cov_{res}.bin", cv.astype(np.uint8))}
        shutil.rmtree(tmp, ignore_errors=True)
    dump("job.json", {"bbox": bbox, "tile_px": TILE, "dst_crs": CRS, "runs": jobs})

    total = sum(os.path.getsize(os.path.join(OUT, n)) for n in os.listdir(OUT))
    print("total fixture bytes:", total)


if __name__ == "__main__":
    main()

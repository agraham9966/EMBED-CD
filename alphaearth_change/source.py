"""AlphaEarth (Google Satellite Embedding V1) as a change source: anonymous windowed COG reads.

Replaces geotessera for AlphaEarth Change. Why: TESSERA only has dense global coverage for 2024,
and a change map needs two dense years. AlphaEarth is global and complete for 2017-2025, 64-dim,
10 m, public COGs on source.coop (CC-BY 4.0, no account, no Earth Engine).

Three facts about this dataset drive the whole design:

1. **The COGs are bottom-up.** Origin is the BOTTOM-left, y-resolution is POSITIVE. The bucket
   ships a sidecar `.vrt` that flips them, but that VRT points at `/vsis3/` (needs AWS config)
   and is a warped dataset (slower). Reading the `.tiff` over `/vsicurl/` and flipping rows in
   numpy is byte-identical to the VRT (verified) and skips both problems.
2. **Pixels are int8 and must be de-quantized** as `(v/127.5)**2 * sign(v)`, which yields
   unit-length 64-d vectors. `-128` is nodata (present in one channel => present in all).
3. **Reads MUST be block-aligned.** Blocks are 1024x1024 and PIXEL-interleaved, so touching one
   pixel pulls all 64 channels of the whole block. An aligned 1024 window reads in ~1s; a
   512-px window straddling four blocks took ~35s. Hence TILE_PX below.

The UTM pixel grid is identical across years (verified: the same file bounds recur for every
year), so a tile is defined by its bounds alone and both years land on the same grid — exactly
the invariant `job.py` already relies on, so nothing downstream needs to align or resample.
"""
import os
import shutil
import urllib.request
from collections import namedtuple

import numpy as np

_BASE = "https://data.source.coop/tge-labs/aef/v1/annual/"
INDEX_URL = _BASE + "aef_index.parquet"
_S3_BASE = "s3://us-west-2.opendata.source.coop/tge-labs/aef/v1/annual/"

_UA = "alphaearth-change/0.5 (QGIS plugin)"
TILE_PX = 1024          # = the COG block size; see fact 3. 10.24 km at native 10 m.
NODATA_RAW = -128
_PARQUET_COLS = ["path", "year", "crs",
                 "utm_west", "utm_south", "utm_east", "utm_north",
                 "wgs84_west", "wgs84_south", "wgs84_east", "wgs84_north"]
_BOUND_COLS = _PARQUET_COLS[3:]
# Stored columns: `key` replaces `path` (the shared URL prefix is implicit). Both string
# columns are fixed-width BYTES, not object arrays — object arrays would force the .npz to be
# pickled, and 300k full URLs as unicode is >100 MB of RAM for nothing.
_INDEX_COLS = ["key", "year", "crs"] + _BOUND_COLS

# A tile is year-independent: bounds in a UTM CRS, on the source's own 10 m grid.
Tile = namedtuple("Tile", "crs west south east north")


def _vsicurl(path):
    """GDAL's virtual filesystem prefix, for http(s) URLs ONLY.

    rasterio inferred this from the scheme; GDAL does not, so the port initially fetched
    nothing. Prefixing unconditionally is the opposite mistake — CURL then tries to parse a
    local path as a URL and rejects "C:" as a port number. The Index's `base` is a local
    directory in tests and an https URL in production, so both cases are real.
    """
    return f"/vsicurl/{path}" if path.startswith(("http://", "https://")) else path


def default_cache_dir():
    return os.path.join(os.path.expanduser("~"), ".cache", "alphaearth")


def _gdal_env():
    """Scoped GDAL config for fast anonymous COG reads. MULTIRANGE/MULTIPLEX are what turn a
    block fetch from tens of seconds into ~1s.

    Scoped, not global: these run inside QGIS, and permanently changing its GDAL settings would
    affect every other layer it draws.
    """
    from . import gdalio as G
    return G.config(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                    GDAL_HTTP_MULTIPLEX="YES", GDAL_HTTP_MULTIRANGE="YES",
                    GDAL_NUM_THREADS="ALL_CPUS", VSI_CACHE="TRUE",
                    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tiff")


class Index:
    """Which COG covers what. The published index is a 78 MB parquet; we download it once and
    keep a ~10 MB .npz of just the columns we need, so later runs load in well under a second
    and work offline."""

    def __init__(self, cache_dir=None, base=_BASE):
        self.cache_dir = cache_dir or default_cache_dir()
        self.base = base            # swap to point at a mirror (tests use a local dir)
        self._d = None

    def row_at(self, d, i):
        """One index row as a plain dict: byte columns decoded, URL rebuilt from the key.
        Only ever called for the handful of rows a job actually touches."""
        r = {k: float(d[k][i]) for k in _BOUND_COLS}
        r["year"] = int(d["year"][i])
        r["crs"] = d["crs"][i].decode()
        r["path"] = self.base + d["key"][i].decode()
        return r

    @property
    def npz_path(self):
        return os.path.join(self.cache_dir, "aef_index.npz")

    def _build(self, progress=None):
        # ponytail: pyarrow only, no fallback — it ships with QGIS's Python and the dev venv.
        # If some install lacks it, GDAL's Parquet driver is the escape hatch.
        import pyarrow.parquet as pq

        os.makedirs(self.cache_dir, exist_ok=True)
        raw = os.path.join(self.cache_dir, "aef_index.parquet")
        if not os.path.exists(raw):
            if progress:
                progress("downloading AlphaEarth tile index (78 MB, one time)")
            tmp = raw + ".part"
            # source.coop 403s the default Python-urllib user-agent
            req = urllib.request.Request(INDEX_URL, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            os.replace(tmp, raw)
        t = pq.read_table(raw, columns=_PARQUET_COLS)
        col = {c: t[c].to_numpy(zero_copy_only=False) for c in _PARQUET_COLS}
        d = {c: col[c].astype(np.float64) for c in _BOUND_COLS}
        d["year"] = col["year"].astype(np.int32)
        d["crs"] = np.array([str(c) for c in col["crs"]], dtype="S")
        d["key"] = np.array([str(p)[len(_S3_BASE):] for p in col["path"]], dtype="S")
        np.savez_compressed(self.npz_path, **d)
        os.remove(raw)
        return d

    def load(self, progress=None):
        if self._d is None:
            if os.path.exists(self.npz_path):
                with np.load(self.npz_path, allow_pickle=False) as z:
                    self._d = {k: z[k] for k in z.files}
            else:
                self._d = self._build(progress)
        return self._d

    def years(self):
        return sorted(set(int(y) for y in self.load()["year"]))

    def covering(self, bbox_4326, year):
        """Index rows whose footprint overlaps a lon/lat bbox, for one year."""
        d = self.load()
        w, s, e, n = bbox_4326
        hit = ((d["year"] == year)
               & (d["wgs84_west"] < e) & (d["wgs84_east"] > w)
               & (d["wgs84_south"] < n) & (d["wgs84_north"] > s))
        return [self.row_at(d, i) for i in np.flatnonzero(hit)]


class AlphaEarthSource:
    """The change job's view of AlphaEarth: list tiles for an area, fetch a tile for a year."""

    def __init__(self, cache_dir=None, tile_px=TILE_PX, index=None):
        self.index = index or Index(cache_dir)
        self.tile_px = tile_px
        self._by_zone = {}

    def years(self):
        return self.index.years()

    def list_tiles(self, bbox_4326, year_a, year_b):
        """(all, both, partial) block-aligned tiles covering the bbox — same contract as the
        old TESSERA `list_tiles`. Tiles are keyed by bounds, so a tile present in only one year
        still gets processed and reported through the coverage band."""
        a = self._tiles_for(bbox_4326, year_a)
        b = self._tiles_for(bbox_4326, year_b)
        return sorted(a | b), sorted(a & b), sorted(a ^ b)

    def _tiles_for(self, bbox_4326, year):
        """Chop each covering COG's overlap with the bbox into tile_px-aligned windows.
        Alignment is to the COG's own origin, so every window is a whole block (see fact 3)."""
        from .gdalio import transform_bounds

        tiles = set()
        step = self.tile_px * 10.0                     # native resolution is 10 m
        for r in self.index.covering(bbox_4326, year):
            crs = str(r["crs"])
            left, bottom, right, top = transform_bounds("EPSG:4326", crs, *bbox_4326)
            x0, y0 = float(r["utm_west"]), float(r["utm_south"])
            # clip the AOI to this COG, then snap outward to the COG's block grid
            left = max(left, x0)
            bottom = max(bottom, y0)
            right = min(right, float(r["utm_east"]))
            top = min(top, float(r["utm_north"]))
            if right <= left or top <= bottom:
                continue
            c0 = int(np.floor((left - x0) / step))
            c1 = int(np.ceil((right - x0) / step))
            r0 = int(np.floor((bottom - y0) / step))
            r1 = int(np.ceil((top - y0) / step))
            for cy in range(r0, r1):
                for cx in range(c0, c1):
                    w = x0 + cx * step
                    s = y0 + cy * step
                    tiles.add(Tile(crs, w, s, min(w + step, float(r["utm_east"])),
                                   min(s + step, float(r["utm_north"]))))
        return tiles

    def fetch(self, tile, year):
        """(array [H,W,64] float32, crs, transform) for a tile in a year, or (None, None, None)
        if that year has no COG here. De-quantized to unit-length vectors; nodata pixels come
        back as all-zero, which is exactly what `score.valid_mask` treats as unusable."""
        from . import gdalio as G

        row = self._row_for(tile, year)
        if row is None:
            return None, None, None
        with _gdal_env():
            # GDAL needs the /vsicurl/ prefix spelled out. rasterio recognised a bare https URL
            # and added it silently, so the port looked fine against the local-file tests and
            # fetched precisely nothing from the real bucket.
            ds = G.open_ds(_vsicurl(row["path"]))
            x0, y0 = float(row["utm_west"]), float(row["utm_south"])   # bottom-left (fact 1)
            col_off = int(round((tile.west - x0) / 10.0))
            row_off = int(round((tile.south - y0) / 10.0))             # rows count NORTHWARD
            width = int(round((tile.east - tile.west) / 10.0))
            height = int(round((tile.north - tile.south) / 10.0))
            width = min(width, ds.RasterXSize - col_off)
            height = min(height, ds.RasterYSize - row_off)
            if width <= 0 or height <= 0:
                return None, None, None
            raw = G.read_window(ds, col_off, row_off, width, height)   # [64,H,W] int8, south-up
            ds = None

        raw = raw[:, ::-1, :]                                          # flip to north-up
        cube = _dequantize(np.moveaxis(raw, 0, -1))                    # -> [H,W,64] float32
        transform = G.Transform.from_origin(tile.west, tile.south + height * 10.0, 10.0, 10.0)
        return cube, str(row["crs"]), transform

    def _row_for(self, tile, year):
        """The COG containing this tile. Rows for a (year, crs) are memoized because a job
        calls this twice per tile and the full index is ~300k rows."""
        key = (year, tile.crs)
        rows = self._by_zone.get(key)
        if rows is None:
            d = self.index.load()
            hit = (d["year"] == year) & (d["crs"] == tile.crs.encode())
            rows = [self.index.row_at(d, i) for i in np.flatnonzero(hit)]
            self._by_zone[key] = rows
        for r in rows:
            if (float(r["utm_west"]) <= tile.west and float(r["utm_south"]) <= tile.south
                    and float(r["utm_east"]) >= tile.east
                    and float(r["utm_north"]) >= tile.north):
                return r
        return None


def _dequantize(raw):
    """int8 -> the analysis-ready embedding: (v/127.5)**2 * sign(v). Nodata (-128) becomes an
    all-zero vector so downstream validity checks (norm > 0) catch it without a special case."""
    v = raw.astype(np.float32)
    out = (v / 127.5) ** 2 * np.sign(v)
    out[(raw == NODATA_RAW).any(axis=-1)] = 0.0
    return out

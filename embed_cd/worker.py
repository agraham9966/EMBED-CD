"""Subprocess entry point for a change job.

Runs out-of-process because PROJ/GDAL work is unsafe on QGIS's own threads, and because a
long job must never block the UI. Speaks a line protocol on stdout:

  PLAN <tiles> <download_bytes> <width> <height>   once, before any download
  TILE <done> <total> <vrt_path>                   after each tile lands (VRT rewritten)
  AUTO <threshold> <fraction_above>                once, when finished
  ERR  <message>
  OK

Usage: python -m embed_cd.worker '<json spec>'
"""
import json
import os
import site
import sys


def _enable_user_site():
    """QGIS runs with the per-user site-packages OFF, and a subprocess inherits that. A
    non-admin `pip install` lands exactly there, so put it back on the path."""
    for d in filter(None, [site.getusersitepackages()] if hasattr(site, "getusersitepackages")
                    else []):
        if isinstance(d, str) and os.path.isdir(d) and d not in sys.path:
            site.addsitedir(d)


def _have(module):
    import importlib.util
    return importlib.util.find_spec(module) is not None


def main():
    spec = json.loads(sys.argv[1])
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _enable_user_site()
    # pyarrow reads the one-time tile index. It is NOT refused outright when missing: GDAL's
    # own Parquet driver can do the same job, and asking for a pip step the user may not need
    # is exactly the friction dropping rasterio was meant to remove. Only stop when NEITHER
    # reader exists — and say so in terms of the platform they are actually on, because the old
    # message sent Linux users to OSGeo4W Setup, which does not exist there.
    if not _have("pyarrow"):
        from embed_cd import source as _SRC
        from embed_cd import gdalio as _G
        try:
            has_gdal_parquet = _G.ogr_has_driver("Parquet")
        except Exception:
            has_gdal_parquet = False
        if not has_gdal_parquet:
            print("ERR the tile index needs either pyarrow or a GDAL built with the Parquet "
                  "driver, and this QGIS has neither. " + _SRC.install_hint(), flush=True)
            return 3
    from embed_cd import job, score, vrt

    out_dir = spec["out_dir"]
    name = spec.get("name", "change")
    vrt_path = os.path.join(out_dir, name + ".vrt")
    stop_file = os.path.join(out_dir, ".cancel")

    try:
        # The source must know the target resolution BEFORE listing tiles: a coarse job
        # reads an overview, and a tile then covers proportionally more ground.
        src = job.open_source(spec.get("cache_dir"), spec["res_m"])
        bbox = tuple(spec["bbox"])
        all_tiles, both, partial = job.list_tiles(src, bbox, spec["year_a"], spec["year_b"])
        if not all_tiles:
            print("ERR No AlphaEarth tiles cover this area in either year. Try a different "
                  "area.", flush=True)
            return 2
        import embed_cd.grid as G
        g = G.make_grid(bbox, spec["dst_crs"], spec["res_m"])
        est = G.estimate(g, len(both) or len(all_tiles))
        # partial tiles still produce a result (a nodata class), so report both counts
        print(f"PLAN {len(all_tiles)} {est['download_bytes']} {g.width} {g.height} {len(partial)}", flush=True)
        if spec.get("plan_only"):
            return 0

        os.makedirs(out_dir, exist_ok=True)
        seen = []

        def on_tile(done, total, rec, hist_total):
            # A NEW file per tile, never a rewrite of the same one. QGIS caches the opened
            # dataset per layer and neither reloadData() nor setDataSource() to the same path
            # dislodges it — measured — so rewriting in place leaves the canvas showing whatever
            # revision it first opened. That is the "tiles don't appear until you zoom in and
            # out" behaviour. Only a path it has never seen forces a real reopen.
            seen.append(rec)
            rev = os.path.join(out_dir, f"{name}.v{done}.vrt")
            vrt.write_vrt(rev, g, seen)
            print(f"TILE {done} {total} {rev}", flush=True)
            older = os.path.join(out_dir, f"{name}.v{done - 1}.vrt")
            if os.path.exists(older):
                try:
                    os.remove(older)         # keep only the live one and the canonical
                except OSError:
                    pass                     # still open on Windows; the final sweep gets it

        g2, records, hist, partial = job.run(
            bbox, spec["year_a"], spec["year_b"], out_dir,
            dst_crs=spec["dst_crs"], res_m=spec["res_m"],
            cache_dir=spec.get("cache_dir"), on_tile=on_tile,
            should_stop=lambda: os.path.exists(stop_file),
            cell_m=spec.get("cell_m"),
        )
        if not records:
            # Say WHAT was tried, not just that it failed. This message used to be the entire
            # output of a failed run, and the commonest cause — an output CRS that cannot
            # express the resolution in metres, so the grid collapses to a pixel or two and
            # every tile falls outside it — was indistinguishable from a network problem.
            why = ""
            if min(g.width, g.height) < 4:
                why = (f" The {g.width}x{g.height} px output grid is too small to hold any of "
                       f"them, which happens when the output CRS ({spec['dst_crs']}) is not in "
                       f"metres — Detail is metres, so in a degrees-based CRS it is read as "
                       f"degrees.")
            print(f"ERR No tiles produced a result. {len(all_tiles)} tile(s) cover this area "
                  f"but none landed in the {g.width}x{g.height} px output grid.{why}", flush=True)
            return 2
        # The canonical name is written for the user (a stable file to reopen), but the LAYER
        # stays on the last revision — pointing it back at a path it has already opened this
        # session would resurrect the stale-dataset problem.
        vrt.write_vrt(vrt_path, g2, records)
        # Remove superseded revisions by EXACT name. A prefix test does not work here:
        # "change.vrt".startswith("change.v") is true, so matching on the prefix deletes the
        # canonical file this just wrote — which it did, until the end-to-end run caught it.
        for i in range(1, len(records)):
            stale = os.path.join(out_dir, f"{name}.v{i}.vrt")
            if os.path.exists(stale):
                try:
                    os.remove(stale)
                except OSError:
                    pass                     # still open; harmless, it is a few KB
        t = score.otsu_from_histogram(hist)
        print(f"AUTO {t:.6f} {score.fraction_above(hist, t):.6f}", flush=True)
        print("OK", flush=True)
        return 0
    except Exception as exc:                       # surface, don't traceback into the UI
        print(f"ERR {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

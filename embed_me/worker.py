"""Subprocess entry point for a change job.

Runs out-of-process because PROJ/GDAL work is unsafe on QGIS's own threads, and because a
long job must never block the UI. Speaks a line protocol on stdout:

  PLAN <tiles> <download_bytes> <width> <height>   once, before any download
  TILE <done> <total> <vrt_path>                   after each tile lands (VRT rewritten)
  AUTO <threshold> <fraction_above>                once, when finished
  ERR  <message>
  OK

Usage: python -m embed_me.worker '<json spec>'
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
    missing = [m for m in ("pyarrow",) if not _have(m)]
    if missing:
        # Only pyarrow, and only for the one-time tile index. Everything else this needs —
        # numpy, scipy and GDAL — ships with QGIS, which is the point of having dropped
        # rasterio: the plugin should install and work with no pip step at all.
        print(f"ERR {' and '.join(missing)} not installed in QGIS's Python. Install via OSGeo4W "
              f"Setup (Advanced Install -> python3-{missing[0]}), or open the OSGeo4W Shell and "
              f"run:  python -m pip install {' '.join(missing)}", flush=True)
        return 3
    from embed_me import job, score, vrt

    out_dir = spec["out_dir"]
    name = spec.get("name", "change")
    vrt_path = os.path.join(out_dir, name + ".vrt")
    stop_file = os.path.join(out_dir, ".cancel")

    try:
        src = job.open_source(spec.get("cache_dir"))
        bbox = tuple(spec["bbox"])
        all_tiles, both, partial = job.list_tiles(src, bbox, spec["year_a"], spec["year_b"])
        if not all_tiles:
            print("ERR No AlphaEarth tiles cover this area in either year. Try a different "
                  "area.", flush=True)
            return 2
        import embed_me.grid as G
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
            cell_px=spec.get("cell_px"),
        )
        if not records:
            print("ERR No tiles produced a result.", flush=True)
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

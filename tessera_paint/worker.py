"""Fetch worker, run as a subprocess by the QGIS plugin.

Why a subprocess: pyproj/PROJ segfaults on a QgsTask worker thread inside QGIS, and a
main-thread fetch freezes the UI. A separate process gets its own safe PROJ context and
leaves QGIS fully responsive.

Protocol (stdout, line-based):
  PROG <current> <total> <status...>   progress updates
  ERR <message...>                     failure (also nonzero exit code)
  OK                                   success; mosaic written to spec["out"] (.npz with
                                       mosaic float32 [H,W,128], transform a..f, crs str)

Usage: python worker.py '<json spec>'
  spec = {bbox: [minlon,minlat,maxlon,maxlat], year, max_tiles, target_crs, cache_dir, out}
"""
import json
import os
import sys


def main():
    spec = json.loads(sys.argv[1])
    # make the package importable whether vendored (inside the plugin) or at repo root
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import numpy as np
    from tessera_paint.fetch import load_region, NoCoverage, AoiTooLarge

    def cb(current, total, status=""):
        print(f"PROG {current} {total} {status}", flush=True)
        return True

    try:
        mosaic, transform, crs = load_region(
            tuple(spec["bbox"]), year=spec["year"], max_tiles=spec["max_tiles"],
            target_crs=spec["target_crs"], cache_dir=spec["cache_dir"],
            progress_callback=cb, downsample=spec.get("downsample", 1),
        )
    except (NoCoverage, AoiTooLarge) as exc:
        print(f"ERR {exc}", flush=True)
        return 2
    except Exception as exc:
        print(f"ERR {type(exc).__name__}: {exc}", flush=True)
        return 1

    t = transform
    # raw .npy for the big array (no zip container -> faster both ways) + a tiny json sidecar
    np.save(spec["out"] + ".npy", np.asarray(mosaic, dtype=np.float32))
    with open(spec["out"] + ".json", "w") as f:
        json.dump({"transform": [t.a, t.b, t.c, t.d, t.e, t.f], "crs": str(crs)}, f)
    print("OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

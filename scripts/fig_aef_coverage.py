"""Figure: where AlphaEarth actually has tiles, and how that changes between years.

    python scripts/fig_aef_coverage.py

Writes docs/assets/aef-coverage.png and .svg.

WHY THIS FIGURE EXISTS
----------------------
Nobody publishes a coverage map for this dataset. Google's catalogue says only "terrestrial
land surfaces and shallow waters" and "coverage at the poles is limited"; the STAC bbox is a
useless -90/-180/90/180. So the map is drawn from the published tile index itself, which we
already download for every job.

Every landmass in the image is drawn entirely by AlphaEarth's own tile footprints. There is no
basemap and no coastline file underneath — the shape of the continents IS the data, which is
what makes the picture worth trusting.

Two honest limits, both stated in the caption on the site:
  * a footprint is the COG's full bounding box (~82 km), so coasts read fatter than the real
    valid-pixel extent. Fine for extent, wrong for anything measured.
  * antimeridian-wrapping footprints are dropped rather than split; at 0.25 deg they would
    paint a whole row. There are none in the current index, but the guard stays.
"""
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from embed_cd import source                                        # noqa: E402

RES = 0.25                                  # degrees per cell; enough to read, cheap to build
EARLY, LATE = 2017, 2025
COVERED, ADDED = "#166b6b", "#e8622a"
INK, MUTED = "#20262b", "#5b6570"


def footprint_mask(d, year):
    """[H,W] bool: cells any tile footprint touches, in plate carree."""
    h, w_ = int(180 / RES), int(360 / RES)
    m = d["year"] == year
    west, east = d["wgs84_west"][m], d["wgs84_east"][m]
    south, north = d["wgs84_south"][m], d["wgs84_north"][m]
    ok = east > west                                               # see module docstring
    west, east, south, north = west[ok], east[ok], south[ok], north[ok]

    g = np.zeros((h, w_), bool)
    c0 = np.clip(((west + 180) / RES).astype(int), 0, w_ - 1)
    c1 = np.clip(np.ceil((east + 180) / RES).astype(int), 1, w_)
    r0 = np.clip(((90 - north) / RES).astype(int), 0, h - 1)
    r1 = np.clip(np.ceil((90 - south) / RES).astype(int), 1, h)
    for a, b, c, e in zip(r0, r1, c0, c1):
        g[a:b, c:e] = True
    return g, int(ok.sum()), int((~ok).sum())


def main(out_dir=None):
    out_dir = out_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "assets")
    os.makedirs(out_dir, exist_ok=True)

    d = source.Index().load(progress=lambda msg: print(msg))
    early, n_early, skipped = footprint_mask(d, EARLY)
    late, n_late, _ = footprint_mask(d, LATE)
    if skipped:
        print("warning: %d antimeridian-wrapping footprints dropped" % skipped)

    h, w_ = late.shape
    rgb = np.ones((h, w_, 3))
    rgb[late] = matplotlib.colors.to_rgb(COVERED)
    rgb[late & ~early] = matplotlib.colors.to_rgb(ADDED)

    fig, ax = plt.subplots(figsize=(12.4, 6.0))
    ax.imshow(rgb, extent=(-180, 180, -90, 90), interpolation="nearest")

    # From the index itself, not the rasterized cells: a 0.25 deg cell edge would round the
    # limit up by an eighth of a degree.
    edge = float(max(np.abs(d["wgs84_north"]).max(), np.abs(d["wgs84_south"]).max()))
    for sign in (1, -1):
        ax.axhline(sign * edge, color=MUTED, lw=0.9, ls=(0, (5, 4)))
    ax.text(178, edge + 1.6, "no tiles beyond %.2f°" % edge, fontsize=9, color=MUTED,
            ha="right", va="bottom")

    ax.set_xticks(range(-180, 181, 60))
    ax.set_yticks(range(-90, 91, 30))
    ax.tick_params(labelsize=8.5, colors=MUTED)
    for sp in ax.spines.values():
        sp.set_color("#ccd4da")
    ax.legend(handles=[Patch(facecolor=COVERED, label="tiles in %d" % LATE),
                       Patch(facecolor=ADDED, label="added since %d" % EARLY)],
              loc="upper left", bbox_to_anchor=(0.0, -0.03), ncol=2, fontsize=9.5,
              frameon=False, handlelength=1.4, columnspacing=1.6)

    fig.tight_layout()
    for ext in ("png", "svg"):
        path = os.path.join(out_dir, "aef-coverage.%s" % ext)
        fig.savefig(path, dpi=150, facecolor="white")
        print("wrote", path)
    plt.close(fig)

    print("%d: %d tiles  |  %d: %d tiles  (+%.1f%%)"
          % (EARLY, n_early, LATE, n_late, 100.0 * (n_late - n_early) / n_early))
    print("cells gained %d, lost %d, edge +/-%.2f deg"
          % (int((late & ~early).sum()), int((early & ~late).sum()), edge))


if __name__ == "__main__":
    main()

"""Figure: objects sit on top of a fixed grid of embedding cells.

Standalone — needs only numpy and matplotlib, and knows nothing about the rest of the repo.

    python scripts/fig_objects_and_grid.py

Writes docs/assets/objects-and-grid.png and .svg.

WHAT THIS FIGURE IS FOR, AND WHAT IT DELIBERATELY LEAVES OUT
-----------------------------------------------------------
It explains ONE thing: the embedding grid is fixed, the objects are whatever the cutoff
produced, and an object takes the cells it happens to land on.

It does NOT show the averaging arithmetic, the change scores, the pixel counts behind each
cell, or two cutoffs side by side. Every one of those was tried and every one made the figure
busy enough to need explaining. They are all one sentence of prose instead.

Two rules that keep it readable:
  * one accent colour for "object", everything else grey — colour means identity, never value
  * no numbers anywhere on the canvas, or it starts to look like a results plot
"""
import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, FancyArrowPatch

# A change field, only ever used to get a realistically ragged object shape. Its values are
# never drawn.
CHANGE = np.array([
    [0.02, 0.03, 0.05, 0.06, 0.04, 0.02, 0.01, 0.01],
    [0.03, 0.08, 0.16, 0.19, 0.12, 0.05, 0.02, 0.01],
    [0.05, 0.14, 0.28, 0.31, 0.22, 0.09, 0.03, 0.02],
    [0.04, 0.11, 0.24, 0.26, 0.18, 0.14, 0.12, 0.04],
    [0.02, 0.06, 0.12, 0.14, 0.11, 0.21, 0.24, 0.08],
    [0.01, 0.03, 0.05, 0.06, 0.05, 0.13, 0.16, 0.05],
])
ROWS, COLS = CHANGE.shape
CUTOFF = 0.20

# Oblique projection: y runs away from the viewer, up and to the right.
SX, SY = 0.62, 0.34
Z_TOP, Z_BOTTOM = 3.7, 0.0

ACCENT = "#e8622a"
GRID_FILL, GRID_LINE = "#eef1f3", "#b6bfc7"
SHEET_FILL, SHEET_LINE = "#f7f9fa", "#ccd4da"
INK, MUTED = "#20262b", "#5b6570"


def proj(x, y, z):
    return x + y * SX, z + y * SY


def quad(x, y, z):
    return [proj(x, y, z), proj(x + 1, y, z), proj(x + 1, y + 1, z), proj(x, y + 1, z)]


def components(cut):
    """Connected components (4-connectivity) of cells at or above the cutoff."""
    members = {(r, c) for r in range(ROWS) for c in range(COLS) if CHANGE[r, c] >= cut}
    seen, out = set(), []
    for cell in sorted(members):
        if cell in seen:
            continue
        stack, comp = [cell], []
        seen.add(cell)
        while stack:
            r, c = stack.pop()
            comp.append((r, c))
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = (r + dr, c + dc)
                if nb in members and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        out.append(sorted(comp))
    return out


def outline_segments(comp):
    """Every cell edge bordering a non-member, in grid coordinates."""
    s, segs = set(comp), []
    for r, c in comp:
        x, y = c, ROWS - 1 - r          # flip so row 0 draws at the far side
        if (r - 1, c) not in s:
            segs.append(((x, y + 1), (x + 1, y + 1)))
        if (r + 1, c) not in s:
            segs.append(((x, y), (x + 1, y)))
        if (r, c - 1) not in s:
            segs.append(((x, y), (x, y + 1)))
        if (r, c + 1) not in s:
            segs.append(((x + 1, y), (x + 1, y + 1)))
    return segs


def draw_sheet(ax, z, fill, edge, z0):
    """The faint parallelogram that makes a layer read as a sheet."""
    ax.add_patch(Polygon([proj(0, 0, z), proj(COLS, 0, z),
                          proj(COLS, ROWS, z), proj(0, ROWS, z)],
                         closed=True, facecolor=fill, edgecolor=edge, lw=1.2, zorder=z0))


def main(out_dir=None):
    out_dir = out_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "assets")
    os.makedirs(out_dir, exist_ok=True)

    comps = components(CUTOFF)
    covered = {cell for comp in comps for cell in comp}

    fig, ax = plt.subplots(figsize=(10.2, 5.0))

    # ---------- lower layer: the embedding grid ----------------------------------------
    draw_sheet(ax, Z_BOTTOM, GRID_FILL, SHEET_LINE, 1)
    for r in range(ROWS):
        for c in range(COLS):
            x, y = c, ROWS - 1 - r
            lit = (r, c) in covered
            ax.add_patch(Polygon(quad(x, y, Z_BOTTOM), closed=True,
                                 facecolor=ACCENT if lit else GRID_FILL,
                                 alpha=0.30 if lit else 1.0,
                                 edgecolor=GRID_LINE, lw=0.8, zorder=2))

    # ---------- the reach from one layer to the other -----------------------------------
    big = max(comps, key=len)
    br = np.mean([ROWS - 1 - r for r, _ in big]) + 0.5
    bc = np.mean([c for _, c in big]) + 0.5
    ax.add_patch(FancyArrowPatch(proj(bc, br, Z_TOP - 0.25), proj(bc, br, Z_BOTTOM + 0.55),
                                 arrowstyle="-|>", mutation_scale=17, lw=1.6,
                                 color=MUTED, zorder=4, shrinkA=0, shrinkB=0))
    ax.text(*proj(bc + 0.35, br, (Z_TOP + Z_BOTTOM) / 2 + 0.1),
            "takes the cells\nit lands on", fontsize=10, color=MUTED, va="center",
            ha="left", linespacing=1.4, zorder=4)

    # ---------- upper layer: the objects, outlines only ---------------------------------
    draw_sheet(ax, Z_TOP, SHEET_FILL, SHEET_LINE, 5)
    for comp in comps:
        for r, c in comp:                                     # translucent body
            x, y = c, ROWS - 1 - r
            ax.add_patch(Polygon(quad(x, y, Z_TOP), closed=True, facecolor=ACCENT,
                                 alpha=0.22, edgecolor="none", zorder=6))
        for (p, q) in outline_segments(comp):                 # crisp jagged boundary
            xs, ys = zip(proj(p[0], p[1], Z_TOP), proj(q[0], q[1], Z_TOP))
            ax.plot(xs, ys, color=ACCENT, lw=2.6, solid_capstyle="projecting", zorder=7)

    # ---------- labels -------------------------------------------------------------------
    mid = ROWS * SY / 2
    ax.text(-0.6, Z_TOP + mid + 0.16, "objects", fontsize=13, fontweight="bold",
            color=ACCENT, ha="right", va="bottom")
    ax.text(-0.6, Z_TOP + mid - 0.12, "whatever the cutoff produced", fontsize=10,
            color=MUTED, ha="right", va="top")
    ax.text(-0.6, Z_BOTTOM + mid + 0.16, "embedding grid", fontsize=13, fontweight="bold",
            color=INK, ha="right", va="bottom")
    ax.text(-0.6, Z_BOTTOM + mid - 0.12, "fixed 160 m cells, computed once", fontsize=10,
            color=MUTED, ha="right", va="top")

    ax.set_xlim(-6.4, COLS + ROWS * SX + 0.4)
    ax.set_ylim(-0.35, Z_TOP + ROWS * SY + 0.35)
    ax.set_aspect("equal")
    ax.axis("off")

    fig.text(0.5, 0.05,
             "Raise the cutoff and the outlines change. The grid underneath does not.",
             ha="center", fontsize=11, color=INK)
    fig.subplots_adjust(left=0.01, right=0.99, top=0.99, bottom=0.13)

    for ext in ("png", "svg"):
        path = os.path.join(out_dir, "objects-and-grid.%s" % ext)
        fig.savefig(path, dpi=170, facecolor="white")
        print("wrote", path)
    plt.close(fig)
    print("%d object(s), sizes %s" % (len(comps), [len(c) for c in comps]))


if __name__ == "__main__":
    main()

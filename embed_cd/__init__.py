"""EMBED-CD — tiled year-over-year change maps from AlphaEarth satellite embeddings.

Streams tile by tile: read two years of one tile, reduce 2x64 bands to a single change
band, reproject only that band into the job's output grid, write it, free the embeddings.
Memory stays at one tile regardless of area, and results appear in QGIS as they land.

Data: Google / Google DeepMind's AlphaEarth Foundations Satellite Embedding V1, published as
public COGs on source.coop (CC-BY 4.0) — global and complete 2017-2025, no account needed.

USING THIS WITHOUT QGIS
-----------------------
Nothing here imports QGIS or Qt; the plugin is a separate shell around this package. The whole
pipeline runs from a script:

    from embed_cd import job
    grid, tiles, hist, partial = job.run(
        bbox=(-125.4, 49.6, -125.2, 49.8), year_a=2019, year_b=2024,
        out_dir="run", dst_crs="EPSG:32610", res_m=10.0, cell_m=160.0)

GDAL is imported lazily, inside the functions that need it, so `score` and `head` are usable
in a plain numpy/scipy environment with no GDAL installed at all:

    score   change_score / histogram / otsu_from_histogram, on ANY two [H, W, 64] cubes
    head    OvRHead, the one-vs-rest classifier, on ANY [A, B] feature vectors

Everything else (grid, gdalio, source, job, objects, cells, store) needs GDAL at call time.
"""
from . import basemap, cells, gdalio, grid, head, job, objects, score, source, store, vrt

__all__ = ["basemap", "cells", "gdalio", "grid", "head", "job", "objects", "score",
           "source", "store", "vrt"]

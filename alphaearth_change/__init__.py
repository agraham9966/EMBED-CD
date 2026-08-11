"""AlphaEarth Change — tiled year-over-year change maps from AlphaEarth satellite embeddings.

Streams tile by tile: read two years of one tile, reduce 2x64 bands to a single change
band, reproject only that band into the job's output grid, write it, free the embeddings.
Memory stays at one tile regardless of area, and results appear in QGIS as they land.

Data: Google / Google DeepMind's AlphaEarth Foundations Satellite Embedding V1, published as
public COGs on source.coop (CC-BY 4.0) — global and complete 2017-2025, no account needed.
"""
from . import score, grid, vrt

__all__ = ["score", "grid", "vrt"]

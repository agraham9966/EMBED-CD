"""TESSERA Paint engine: fetch precomputed pixel embeddings, run similarity/masking.

QGIS-free by design so the logic is testable without QGIS. The plugin is a thin shell.
"""

from .sim import similarity, mask, standardize_stats, mosaic_stats, prepare, score
from .fetch import load_region, NoCoverage, AoiTooLarge
from .viz import pca_rgb
from . import budget, change

__all__ = ["similarity", "mask", "standardize_stats", "mosaic_stats", "prepare", "score",
           "load_region", "NoCoverage", "AoiTooLarge", "pca_rgb", "budget", "change"]

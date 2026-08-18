"""One place that imports the engine, vendored copy first.

**Relative-first is not a style choice.** A top-level `embed_cd`, once imported, stays
in `sys.modules` for the life of the QGIS process. Reinstalling the plugin purges only the
plugin's OWN package (`embed_cd_qgis.*`), so the top-level engine from the PREVIOUS
version survives and the new UI runs against it. That shipped once, in 0.7.1: a current
`classify.py` calling a stale `head.py`, failing with "unexpected keyword argument 'pool'" —
a message that points at the new code while the actual fault is the old code still being loaded.

Importing the engine as a subpackage of the plugin means QGIS's upgrader purges it along with
everything else, so an upgraded plugin can never run last version's engine. This was learned
once on a sibling plugin and not carried across until it bit here too.

The top-level fallback is for dev mode, where the plugin folder is a symlink into the repo and
there is no vendored copy; `__init__.py` puts the repo root on sys.path for that case.
"""
try:
    from .embed_cd import (                        # noqa: F401  (zip install)
        basemap, cells, gdalio, grid, head, job, objects, score, source, store, vrt)
except ImportError:                                          # dev mode: engine at the repo root
    from embed_cd import (                          # noqa: F401
        basemap, cells, gdalio, grid, head, job, objects, score, source, store, vrt)

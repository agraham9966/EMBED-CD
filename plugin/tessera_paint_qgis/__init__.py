"""TESSERA Paint QGIS plugin entry point.

Finds the `tessera_paint` engine one of two ways:
- vendored copy sitting right next to this file (releases/*.zip built by
  scripts/make_release.py put it here) -> add this directory to sys.path.
- dev mode: no vendored copy, so fall back to the repo root two levels up
  (works when this plugin folder is a symlink into the live repo).
"""
import os
import site
import sys

# QGIS on Windows runs with the per-user site-packages OFF sys.path. When deps were
# pip-installed without admin (pip falls back to --user because Program Files isn't
# writable), geotessera/rasterio land there and QGIS can't see them. Force-add it.
_USERSITE = site.getusersitepackages()
if _USERSITE and os.path.isdir(_USERSITE):
    site.addsitedir(_USERSITE)

_HERE = os.path.dirname(__file__)
if not os.path.isdir(os.path.join(_HERE, "tessera_paint")):
    # dev mode only: engine lives at the repo root (this folder is a symlink into the
    # repo). Zip installs vendor the engine as a subpackage and import it relatively,
    # so no sys.path entry is needed (or wanted — top-level modules survive plugin
    # upgrades in sys.modules and go stale).
    _REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)


def classFactory(iface):
    from .plugin import TesseraPaintPlugin
    return TesseraPaintPlugin(iface)

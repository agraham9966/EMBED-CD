"""EMBED-ME — QGIS plugin entry point."""
import os
import site
import sys

# QGIS on Windows runs with the per-user site-packages OFF sys.path, so a non-admin
# a non-admin `pip install` would be invisible. Put it back.
_USERSITE = site.getusersitepackages()
if _USERSITE and os.path.isdir(_USERSITE):
    site.addsitedir(_USERSITE)

_HERE = os.path.dirname(__file__)
if not os.path.isdir(os.path.join(_HERE, "embed_me")):
    # dev mode: engine lives at the repo root (this folder symlinked into the plugins dir)
    _ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)


def classFactory(iface):
    from .plugin import EmbedMePlugin
    return EmbedMePlugin(iface)

"""Build a self-contained, versioned QGIS plugin zip for 'Install from ZIP'.

Vendors the plugin's engine package into a staging copy of the plugin folder (so the zip
needs nothing from outside itself), then zips it to releases/<plugin>-<version>.zip. The
version is read from that plugin's metadata.txt.

Usage:
    python scripts/make_release.py            # build every plugin
    python scripts/make_release.py change     # just EMBED-CD
    python scripts/make_release.py paint      # just Tessera Paint
"""
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RELEASES_DIR = ROOT / "releases"

# short name -> (plugin folder, engine package vendored inside it)
PLUGINS = {
    "paint": ("tessera_paint_qgis", "tessera_paint"),
    "change": ("embed_cd_qgis", "embed_cd"),
}


def read_version(plugin_dir):
    m = re.search(r"^version=(.+)$", (plugin_dir / "metadata.txt").read_text(), re.MULTILINE)
    if not m:
        raise ValueError(f"version= not found in {plugin_dir}/metadata.txt")
    return m.group(1).strip()


def build(key):
    plugin_name, engine_name = PLUGINS[key]
    plugin_dir = ROOT / "plugin" / plugin_name
    engine_dir = ROOT / engine_name
    version = read_version(plugin_dir)

    staging = RELEASES_DIR / f"_staging-{plugin_name}-{version}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    stage_plugin = staging / plugin_name
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(plugin_dir, stage_plugin, ignore=ignore)
    shutil.copytree(engine_dir, stage_plugin / engine_name, ignore=ignore)
    # icons/ lives at the repo root but has to travel INSIDE the plugin, or an installed copy
    # has no icon to load and silently falls back to text.
    icons = ROOT / "icons"
    if icons.is_dir():
        shutil.copytree(icons, stage_plugin / "icons", ignore=ignore)

    RELEASES_DIR.mkdir(exist_ok=True)
    zip_path = RELEASES_DIR / f"{plugin_name}-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in stage_plugin.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(staging))

    shutil.rmtree(staging)
    print(f"built {zip_path}")
    return zip_path


def main():
    keys = sys.argv[1:] or list(PLUGINS)
    for k in keys:
        if k not in PLUGINS:
            raise SystemExit(f"unknown plugin '{k}'; choose from {', '.join(PLUGINS)}")
        build(k)


if __name__ == "__main__":
    main()

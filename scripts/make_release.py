"""Build a self-contained, versioned QGIS plugin zip, and publish it as a QGIS repository.

Vendors the plugin's engine package into a staging copy of the plugin folder (so the zip
needs nothing from outside itself), then zips it to releases/<plugin>-<version>.zip. The
version is read from that plugin's metadata.txt.

It then writes the same zip and a `plugins.xml` into `docs/`, which the Pages site already
publishes. That turns https://agraham9966.github.io/EMBED-CD/plugins.xml into a QGIS plugin
repository: add it once under Plugins -> Manage and Install -> Settings, and every later
version installs and upgrades in-app. Which matters for the Linux box reachable only over RDP,
where copying a zip across for each build is the slow part of testing.

Usage:
    python scripts/make_release.py
"""
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RELEASES_DIR = ROOT / "releases"
DOCS_DIR = ROOT / "docs"
DOWNLOAD_DIR = DOCS_DIR / "downloads"
SITE_URL = "https://agraham9966.github.io/EMBED-CD"

# short name -> (plugin folder, engine package vendored inside it)
PLUGINS = {
    "change": ("embed_cd_qgis", "embed_cd"),
}


def read_metadata(plugin_dir):
    """metadata.txt as a dict. Its [general] section is flat key=value, and `about` runs to one
    very long line, so a plain per-line split is enough and configparser is not needed."""
    out = {}
    for line in (plugin_dir / "metadata.txt").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith(("[", "#")):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def write_repository_xml(meta, zip_path, plugin_name):
    """A one-plugin QGIS repository, generated from metadata.txt so the two can never disagree.

    `download_url` points at the copy under docs/, because that is what the Pages workflow
    publishes. Only the current version is kept: this is a private channel for testing builds,
    not an archive, and every stale zip is another megabyte in the repo forever.

    The published file is named `<plugin_name>.zip`, WITHOUT the version, and that is not
    cosmetic. QGIS derives the folder it installs into as `file_name.partition(".")[0]` — the
    text up to the first dot. A versioned `embed_cd_qgis-0.40.1.zip` becomes `embed_cd_qgis-0`,
    which does not match the `embed_cd_qgis` folder inside the zip, and the install fails with
    "Plugin has disappeared". A dotless stem sidesteps the whole thing; the version lives in the
    <version> element, which is what QGIS actually reads to offer upgrades.
    """
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    repo_name = f"{plugin_name}.zip"
    for old in DOWNLOAD_DIR.glob("*.zip"):
        if old.name != repo_name:
            old.unlink()
    shutil.copy2(zip_path, DOWNLOAD_DIR / repo_name)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    root = ET.Element("plugins")
    p = ET.SubElement(root, "pyqgis_plugin",
                      name=meta["name"], version=meta["version"], plugin_id="1")
    fields = {
        "description": meta.get("description", ""),
        "about": meta.get("about", ""),
        "version": meta["version"],
        "qgis_minimum_version": meta.get("qgisMinimumVersion", "3.28"),
        "qgis_maximum_version": meta.get("qgisMaximumVersion", "3.99"),
        "homepage": meta.get("homepage", ""),
        "file_name": repo_name,
        "icon": meta.get("icon", ""),
        "author_name": meta.get("author", ""),
        "download_url": f"{SITE_URL}/downloads/{repo_name}",
        "uploaded_by": meta.get("author", ""),
        "create_date": now,
        "update_date": now,
        # Carried through verbatim: an experimental plugin is hidden unless the user ticks
        # "Show also experimental plugins", and silently flipping that here would mean the
        # repository advertised something metadata.txt does not say.
        "experimental": meta.get("experimental", "False"),
        "deprecated": meta.get("deprecated", "False"),
        "tracker": meta.get("tracker", ""),
        "repository": meta.get("repository", ""),
        "tags": meta.get("tags", ""),
    }
    for k, v in fields.items():
        ET.SubElement(p, k).text = v
    ET.indent(root, space="  ")
    out = DOCS_DIR / "plugins.xml"
    out.write_bytes(b'<?xml version="1.0" encoding="UTF-8"?>\n'
                    + ET.tostring(root, encoding="utf-8"))
    print(f"wrote {out}")
    print(f"      repository URL: {SITE_URL}/plugins.xml")
    return out


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
    write_repository_xml(read_metadata(plugin_dir), zip_path, plugin_name)
    return zip_path


def publish_index():
    """Put the pre-built tile index next to the plugin zip, so the site serves it and the plugin
    downloads a 4 MB numpy file instead of an 78 MB parquet that needs pyarrow.

    The index barely changes (only when AlphaEarth publishes a new year), so it is NOT rebuilt on
    every release: kept if already published, unless --refresh-index is passed. A rebuild reuses
    the exact code the client uses (`Index._build`), so the published file and a client-built one
    are byte-for-byte the same artifact.
    """
    from embed_cd import source as SRC

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dst = DOWNLOAD_DIR / "aef_index.npz"
    if dst.exists() and "--refresh-index" not in sys.argv:
        print(f"kept  {dst}  (pass --refresh-index to rebuild from parquet)")
        return

    cached = Path(SRC.default_cache_dir()) / "aef_index.npz"
    if cached.exists() and "--refresh-index" not in sys.argv:
        shutil.copy2(cached, dst)          # the validated artifact every local run has used
        print(f"published {dst}  (from local cache)")
        return

    tmp = RELEASES_DIR / "_index_build"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    SRC.Index(cache_dir=str(tmp))._build(progress=lambda m: print("  ", m))
    shutil.copy2(tmp / "aef_index.npz", dst)
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"published {dst}  (rebuilt from parquet)")


def main():
    keys = sys.argv[1:] or list(PLUGINS)
    for k in keys:
        if k not in PLUGINS:
            raise SystemExit(f"unknown plugin '{k}'; choose from {', '.join(PLUGINS)}")
        build(k)
    publish_index()


if __name__ == "__main__":
    main()

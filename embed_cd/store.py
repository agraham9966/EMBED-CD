"""Persist a run's polygons and labelling progress beside its tiles.

The run folder is already the unit of work: tiles, the VRT and the cell stores all live there
and `Open…` finds them. Polygons and labels join them, so there is no separate "save project"
step to learn or forget — set an output folder and everything survives; don't and it is scratch.

Two files, split by how often they change:

  objects_<a>_<b>.gpkg   geometry + attributes + the embedding as a BLOB. Written ONCE, when
                         the polygons are cut, and never rewritten. It is the expensive
                         artifact (minutes over a large area) and it is immutable.
  labels_<a>_<b>.json    class vectors, colours, per-polygon labels and the threshold they were
                         cut at. Rewritten on every label; a few KB, so that is free.

Predictions and confidence are deliberately NOT stored. They are derived from the classes, so
refitting on load reproduces them exactly — and not storing them is what keeps an 800-row
GeoPackage from being rewritten on every click.

Per-polygon labels only mean anything while the polygon set is unchanged, which is why the
threshold is recorded with them: reopen at the same cut and the labels are restored, reopen at a
different one and they fall back to the class vectors, exactly as a re-polygonize already does.
"""
import json
import os

import numpy as np

from . import objects as OB

_GPKG_LAYER = "change_objects"


def objects_path(out_dir, year_a, year_b):
    return os.path.join(out_dir, f"objects_{year_a}_{year_b}.gpkg")


def labels_path(out_dir, year_a, year_b):
    return os.path.join(out_dir, f"labels_{year_a}_{year_b}.json")


def save_objects(path, polys, vectors, crs):
    """Write the cut polygons and their embeddings. Returns the path.

    Uses OGR directly rather than QGIS so the engine stays importable without a QGIS app, which
    is what lets the whole thing be tested outside QGIS.
    """
    from osgeo import ogr, osr

    ogr.UseExceptions()
    if os.path.exists(path):
        os.remove(path)
    drv = ogr.GetDriverByName("GPKG")
    ds = drv.CreateDataSource(path)
    srs = osr.SpatialReference()
    srs.SetFromUserInput(str(crs))
    lyr = ds.CreateLayer(_GPKG_LAYER, srs, ogr.wkbPolygon)
    for name, typ in (("idx", ogr.OFTInteger), ("area_ha", ogr.OFTReal),
                      ("chg_mean", ogr.OFTReal), ("chg_max", ogr.OFTReal)):
        lyr.CreateField(ogr.FieldDefn(name, typ))
    lyr.CreateField(ogr.FieldDefn("vec", ogr.OFTBinary))

    vectors = np.asarray(vectors, dtype="float32")
    lyr.StartTransaction()
    for i, p in enumerate(polys):
        f = ogr.Feature(lyr.GetLayerDefn())
        f.SetGeometry(ogr.CreateGeometryFromWkt(p["wkt"]))
        f["idx"] = int(i)
        f["area_ha"] = float(p.get("area_ha", 0.0))
        f["chg_mean"] = float(p.get("chg_mean", 0.0))
        f["chg_max"] = float(p.get("chg_max", 0.0))
        if i < len(vectors):
            f.SetFieldBinaryFromHexString("vec", OB.pack_vec(vectors[i]).hex().upper())
        lyr.CreateFeature(f)
        f = None
    lyr.CommitTransaction()
    ds = None
    return path


def load_objects(path):
    """(polys, vectors, crs) as `polygonize` + `attach_vectors` would have returned them.

    Rows come back in `idx` order, not feature order — the same rule the memory provider forced
    on the UI, for the same reason: a vector paired with the wrong polygon is wrong silently.
    """
    from osgeo import ogr

    ogr.UseExceptions()
    ds = ogr.Open(path)
    if ds is None:
        raise OSError(f"cannot open {path}")
    lyr = ds.GetLayer(_GPKG_LAYER)
    srs = lyr.GetSpatialRef()
    crs = None
    if srs is not None:
        srs.AutoIdentifyEPSG()
        code = srs.GetAuthorityCode(None)
        crs = f"EPSG:{code}" if code else srs.ExportToWkt()
    rows = []
    for f in lyr:
        blob = f.GetFieldAsBinary("vec") if f.GetFieldIndex("vec") >= 0 else b""
        rows.append((int(f["idx"]),
                     {"wkt": f.GetGeometryRef().ExportToWkt(),
                      "area_ha": float(f["area_ha"] or 0.0),
                      "chg_mean": float(f["chg_mean"] or 0.0),
                      "chg_max": float(f["chg_max"] or 0.0)},
                     OB.unpack_vec(bytes(blob)) if len(blob) else None))
    ds = None
    rows.sort(key=lambda r: r[0])
    polys = [r[1] for r in rows]
    width = max((len(r[2]) for r in rows if r[2] is not None), default=0)
    vectors = np.zeros((len(rows), width), dtype="float32")
    for i, r in enumerate(rows):
        if r[2] is not None:
            vectors[i, :len(r[2])] = r[2]
    return polys, vectors, crs


def save_labels(path, class_vectors, colors, labels, threshold, names=None):
    """`labels` is row -> class for the CURRENT polygon set; `class_vectors` is the banked
    examples that outlive it. Both are stored: the bank is the durable training set, the row map
    is only restorable while the cut is unchanged.

    `names` is the ordered class list, and it is stored SEPARATELY on purpose. A class whose
    examples are all still in `labels` has no banked vectors yet, so deriving the class list
    from `class_vectors` silently loses it — which is every class in a fresh session. Storing
    the merged view instead would fix that but double-count those examples at fit time, since
    the panel merges banked and current itself.
    """
    payload = {
        "version": 1,
        "threshold": float(threshold),
        "colors": dict(colors or {}),
        "names": list(names) if names else sorted(class_vectors or {}),
        "labels": {str(k): v for k, v in (labels or {}).items()},
        "classes": [{"name": name, "vectors": np.asarray(v, np.float32).tolist()}
                    for name, v in (class_vectors or {}).items() if len(v)],
    }
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)
    return path


def load_labels(path):
    """(class_vectors, colors, labels, threshold, names)."""
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    class_vectors = {c["name"]: [np.asarray(v, np.float32) for v in c["vectors"]]
                     for c in payload.get("classes", [])}
    labels = {int(k): v for k, v in payload.get("labels", {}).items()}
    names = payload.get("names") or sorted(class_vectors)
    return class_vectors, payload.get("colors", {}), labels, payload.get("threshold"), names


def meta_path(out_dir):
    return os.path.join(out_dir, "run.json")


def save_meta(out_dir, **fields):
    """Whatever the folder cannot work out about itself. Years come from the VRT filename and
    the extent from the raster, but the NAME a user gave an area exists nowhere on disk — so
    reopening a run called "alex test" showed it as its coordinates instead."""
    path = meta_path(out_dir)
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data.update({k: v for k, v in fields.items() if v is not None})
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    return path


def load_meta(out_dir):
    try:
        with open(meta_path(out_dir), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

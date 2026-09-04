"""The slice of GDAL this project needs, in roughly rasterio's shape.

Why not rasterio: it is the ONLY dependency QGIS does not ship. numpy, scipy, pyarrow, shapely
and GDAL itself all come with every install, so dropping rasterio makes the plugin need no
`pip install` at all — which for a plugin aimed at general QGIS users is the difference between
"install and it works" and "install, hit an error, find the OSGeo4W shell".

It also removes a real hazard: rasterio bundles its OWN copy of GDAL, so using it inside QGIS
means two GDAL builds loaded in one process. That has never bitten here, but it is the kind of
thing that bites once and expensively.

Measured before committing to it, on this machine inside QGIS's Python:

    remote 1024x1024x64 COG block   rasterio 2.67s   gdal 1.46s   (identical bytes)
    read 131-band cell store            15.7ms       15.4ms
    reproject 1024->1000 average         5.4ms        6.3ms       (100% of pixels equal)
    transform_bounds                     0.26ms       0.31ms

The two places GDAL is slower cost under a millisecond against a per-tile budget of seconds.
"""
from collections import namedtuple

import numpy as np

_DTYPE = {
    "uint8": 1, "int8": 14, "uint16": 2, "int16": 3, "uint32": 4, "int32": 5,
    "float32": 6, "float64": 7,
}


def _gdal():
    from osgeo import gdal
    gdal.UseExceptions()
    return gdal


def _osr():
    from osgeo import osr
    osr.UseExceptions()
    return osr


class Transform(namedtuple("Transform", "a b c d e f")):
    """Same field names and meaning as affine.Affine, so call sites read unchanged:
    `a` x pixel size, `c` x origin, `e` y pixel size (negative for north-up), `f` y origin."""

    __slots__ = ()

    @classmethod
    def from_origin(cls, x, y, xres, yres):
        return cls(xres, 0.0, x, 0.0, -yres, y)

    @classmethod
    def from_gt(cls, gt):
        """GDAL's geotransform is (originX, pxW, rowRot, originY, colRot, pxH) — a different
        order from Affine, which is the easiest thing in this whole file to get wrong."""
        return cls(gt[1], gt[2], gt[0], gt[4], gt[5], gt[3])

    def to_gt(self):
        return (self.c, self.a, self.b, self.f, self.d, self.e)


def array_bounds(height, width, t):
    """(west, south, east, north) of an array placed by `t`."""
    return (t.c, t.f + height * t.e, t.c + width * t.a, t.f)


def config(**opts):
    """Scoped GDAL config, the equivalent of rasterio.Env.

    Scoped matters: SetConfigOption is process-global, and inside QGIS that would silently
    change how QGIS's own GDAL behaves for everything else.
    """
    return _gdal().config_options({k: str(v) for k, v in opts.items()})


def srs(crs):
    """osr.SpatialReference from 'EPSG:32610' or a WKT string, in x/y axis order.

    The axis-order call is not optional: without it GDAL 3 hands back lat/lon for geographic
    CRSs and every coordinate comes out transposed.
    """
    osr = _osr()
    s = osr.SpatialReference()
    s.SetFromUserInput(str(crs))
    s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return s


def crs_string(wkt_or_ds):
    """A stable 'EPSG:xxxx' where possible, else WKT — what callers compare and store."""
    wkt = wkt_or_ds if isinstance(wkt_or_ds, str) else wkt_or_ds.GetProjection()
    if not wkt:
        return ""
    s = srs(wkt)
    auth, code = s.GetAuthorityName(None), s.GetAuthorityCode(None)
    return f"{auth}:{code}" if auth and code else wkt


def transform_bounds(src_crs, dst_crs, west, south, east, north, densify=21):
    """Bounding box of a reprojected box. Densified, because a straight edge in one CRS is a
    curve in another and the corners alone understate the extent."""
    ct = _osr().CoordinateTransformation(srs(src_crs), srs(dst_crs))
    return tuple(ct.TransformBounds(west, south, east, north, densify))


def ogr_has_driver(name):
    """Whether this GDAL build carries a given OGR driver. Parquet is opt-in at build time
    (it needs libarrow), so its absence is a normal state to test for, not a failure."""
    ogr = _ogr()
    return ogr.GetDriverByName(name) is not None


def open_vector(path):
    """A vector/tabular dataset, or None if this GDAL has no driver for it. Used for the
    AlphaEarth tile index when pyarrow is absent: GDAL's Parquet driver is opt-in at build
    time, so "no driver" is a real outcome and not an error worth raising here."""
    try:
        return _ogr().Open(path)
    except Exception:
        return None


def open_ds(path, factor=1):
    """`factor` > 1 opens one of the file's built-in overviews instead of the full-res image,
    so the dataset reports the reduced size and reads only the reduced bytes.

    GDAL's OVERVIEW_LEVEL is 0-based over the overviews themselves, so level 0 IS the first
    overview (a 2x reduction) and there is no value meaning "full res" — hence the branch.
    """
    g = _gdal()
    if factor <= 1:
        return g.Open(path)
    level = int(factor).bit_length() - 2          # 2->0, 4->1, 8->2, 16->3
    return g.OpenEx(path, open_options=[f"OVERVIEW_LEVEL={level}"])


def read(path, band=None):
    """(array, crs_string, Transform). Whole file; `band` is 1-based, None reads all bands."""
    ds = open_ds(path)
    arr = ds.ReadAsArray() if band is None else ds.GetRasterBand(band).ReadAsArray()
    return arr, crs_string(ds), Transform.from_gt(ds.GetGeoTransform())


def read_window(ds, xoff, yoff, width, height):
    """[bands, h, w] for a window, matching rasterio's band-first order."""
    arr = ds.ReadAsArray(int(xoff), int(yoff), int(width), int(height))
    return arr if arr.ndim == 3 else arr[None]


def write(path, bands, crs, transform, nodata=None, options=None):
    """Write [bands, h, w] (or [h, w]) as a GeoTIFF. Returns the path."""
    gdal = _gdal()
    arr = np.asarray(bands)
    if arr.ndim == 2:
        arr = arr[None]
    count, height, width = arr.shape
    dtype = _DTYPE[arr.dtype.name]
    opts = [f"{k.upper()}={v}" for k, v in (options or {}).items()]
    ds = gdal.GetDriverByName("GTiff").Create(path, width, height, count, dtype, opts)
    ds.SetGeoTransform(transform.to_gt())
    if crs:
        ds.SetProjection(srs(crs).ExportToWkt())
    for i in range(count):
        band = ds.GetRasterBand(i + 1)
        band.WriteArray(arr[i])
        if nodata is not None:
            band.SetNoDataValue(float(nodata))
    ds.FlushCache()
    del ds
    return path


def mem_ds(arr, crs, transform, nodata=None):
    """An in-memory dataset wrapping one 2-D array, for warping."""
    gdal = _gdal()
    ds = gdal.GetDriverByName("MEM").Create(
        "", arr.shape[1], arr.shape[0], 1, _DTYPE[arr.dtype.name])
    ds.SetGeoTransform(transform.to_gt())
    if crs:
        ds.SetProjection(srs(crs).ExportToWkt())
    band = ds.GetRasterBand(1)
    band.WriteArray(arr)
    if nodata is not None:
        band.SetNoDataValue(float(nodata))
    return ds


def _ogr():
    from osgeo import ogr
    ogr.UseExceptions()
    return ogr


def polygonize(values, mask, crs, transform, connectivity=8):
    """[(wkt, value)] — one polygon per connected run of equal values where `mask` is true."""
    gdal, ogr = _gdal(), _ogr()
    src = mem_ds(values.astype("int32"), crs, transform)
    msk = mem_ds(mask.astype("uint8"), crs, transform)
    vec = ogr.GetDriverByName("Memory").CreateDataSource("poly")
    layer = vec.CreateLayer("poly", srs=srs(crs), geom_type=ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
    gdal.Polygonize(src.GetRasterBand(1), msk.GetRasterBand(1), layer, 0,
                    ["8CONNECTED=8"] if connectivity == 8 else [])
    return [(f.GetGeometryRef().ExportToWkt(), int(f.GetField("v"))) for f in layer]


def rasterize_index(wkts, crs, shape, transform, all_touched=True):
    """Burn many polygons into one int32 array holding 1-based indices, 0 where none.

    One call for the whole set rather than one per polygon: the attach loop is
    O(polygons x cell grids) and was the slow half of "Make polygons". Our polygons come from
    connected components so they never overlap, which is what makes a single index raster a
    faithful representation of all of them at once.
    """
    gdal, ogr = _gdal(), _ogr()
    target = gdal.GetDriverByName("MEM").Create("", shape[1], shape[0], 1, gdal.GDT_Int32)
    target.SetGeoTransform(transform.to_gt())
    target.SetProjection(srs(crs).ExportToWkt())
    vec = ogr.GetDriverByName("Memory").CreateDataSource("r")
    layer = vec.CreateLayer("r", srs=srs(crs), geom_type=ogr.wkbPolygon)
    layer.CreateField(ogr.FieldDefn("i", ogr.OFTInteger))
    defn = layer.GetLayerDefn()
    for i, wkt in enumerate(wkts):
        geom = ogr.CreateGeometryFromWkt(wkt)
        if geom is None:
            continue
        feat = ogr.Feature(defn)
        feat.SetGeometry(geom)
        feat.SetField("i", i + 1)
        layer.CreateFeature(feat)
    gdal.RasterizeLayer(target, [1], layer, options=(
        ["ATTRIBUTE=i"] + (["ALL_TOUCHED=TRUE"] if all_touched else [])))
    return target.GetRasterBand(1).ReadAsArray()


def transform_wkt(wkt, src_crs, dst_crs):
    ogr = _ogr()
    geom = ogr.CreateGeometryFromWkt(wkt)
    geom.Transform(_osr().CoordinateTransformation(srs(src_crs), srs(dst_crs)))
    return geom.ExportToWkt()


def reproject_into(src, src_crs, src_transform, dst_crs, dst_transform, shape, nodata,
                   nearest=False):
    """Warp one band into a new grid, returning the array. `nodata` fills what isn't reached.

    Verified against rasterio.warp.reproject on a real tile: identical to the last bit once
    rasterio is asked for `tolerance=0`. At rasterio's DEFAULT tolerance of 0.125 pixels the two
    disagree by 1.6e-05, because that default uses an approximate transformer and
    ReprojectImage uses the exact one. So this is very slightly MORE accurate than what it
    replaced, by an amount far below the noise floor of an int8-quantised source.
    """
    gdal = _gdal()
    out = np.full(shape, nodata, dtype=src.dtype)
    s_ds = mem_ds(src, src_crs, src_transform, nodata)
    d_ds = mem_ds(out, dst_crs, dst_transform, nodata)
    gdal.ReprojectImage(s_ds, d_ds, None, None,
                        gdal.GRA_NearestNeighbour if nearest else gdal.GRA_Average)
    return d_ds.GetRasterBand(1).ReadAsArray()

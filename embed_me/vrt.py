"""Stitch completed tile GeoTIFFs into one VRT that QGIS can load and refresh.

Written as plain XML on purpose: no GDAL Python bindings needed, trivially testable, and
instant to rewrite as each tile lands (which is what gives the live "fills in as it goes"
canvas). Tiles all share the job's output grid, so sources drop in at exact pixel offsets.

Reading a VRT of *finished* files is safe; reading a single mosaic another process is
mid-write is not — that's why the job writes one file per tile.
"""
import os
import time
from xml.sax.saxutils import escape

# (index, dtype, description, band_nodata, source_nodata, override)
#
# The two nodata values are deliberately DIFFERENT for the coverage band, and that distinction
# is the whole reason this table has two columns:
#
#   source_nodata  -> <NODATA> inside each <ComplexSource>: "this value in THIS TILE means the
#                     tile doesn't really reach here, so don't paint it." Tile windows OVERLAP
#                     (a UTM rectangle warps to a curved quad in the output CRS and the window
#                     is that quad's bbox), so without this a tile's edge-fill paints over its
#                     neighbour's REAL data — which drew a grey "not covered" grid across the
#                     whole mosaic even though every pixel had data.
#   band_nodata    -> <NoDataValue> on the band: "this value means transparent to the reader."
#                     Coverage must NOT have one: 0 (COV_NO_TILE) has to stay a VISIBLE class,
#                     because GDAL fills genuinely uncovered VRT area with 0 and the user needs
#                     to see where we never looked.
#   override       -> a SECOND pass of sources painted after the first, as (source band, its
#                     nodata, LUT). Coverage needs one because source_nodata can only protect
#                     ONE value, and 0 is not the only way a tile says "nothing here". A tile
#                     that overlaps but is genuinely EMPTY reports class 4 (or 2/3), which are
#                     real values, so they paint straight over a neighbour's class 1. That is
#                     not a rare edge: AlphaEarth publishes per UTM zone, so any area near a
#                     zone boundary fetches tiles from BOTH zones and each is entirely empty
#                     over the other's half. Measured at -125.98 (the 9N/10N line): 42% of the
#                     mosaic read "no data in either year" while every one of those pixels had
#                     a valid change value, because EPSG32610 sorts last and painted its
#                     emptiness over 9N's real data.
#
#                     The fix reads the CHANGE band back as coverage: nodata -1 skips every
#                     pixel with no answer, and the LUT maps every valid score to 1. So
#                     wherever there IS a change value, coverage is forced to agree — which is
#                     the invariant the whole layer rests on, now true by construction instead
#                     of by hoping tile order is kind. Where NO tile has data the override
#                     paints nothing and the real 2/3/4 diagnosis still comes through.
_BANDS = (
    (1, "Float32", "change", -1.0, -1.0, None),
    (2, "Byte", "coverage", None, 0, (1, "Float32", -1.0, "-1:0,0:1,1:1")),
)


def write_vrt(path, grid, tiles):
    """tiles: iterable of dicts {path, row0, col0, width, height}. Relative paths are used so
    the folder can be moved."""
    base = os.path.dirname(os.path.abspath(path))
    lines = [f'<VRTDataset rasterXSize="{grid.width}" rasterYSize="{grid.height}">',
             f'  <SRS>{escape(str(grid.crs))}</SRS>',
             '  <GeoTransform>{:.10f}, {:.10f}, 0.0, {:.10f}, 0.0, {:.10f}</GeoTransform>'
             .format(grid.x0, grid.res, grid.y0, -grid.res)]
    def emit(t, src_band, src_dtype, nodata, lut=None):
        rel = os.path.relpath(os.path.abspath(t["path"]), base).replace("\\", "/")
        # ComplexSource (not SimpleSource) because only it accepts <NODATA>; GDAL silently
        # drops a SimpleSource that carries one, which reads back as an empty mosaic.
        lines.append('    <ComplexSource>')
        lines.append(f'      <SourceFilename relativeToVRT="1">{escape(rel)}</SourceFilename>')
        lines.append(f'      <SourceBand>{src_band}</SourceBand>')
        lines.append(f'      <SourceProperties RasterXSize="{t["width"]}" '
                     f'RasterYSize="{t["height"]}" DataType="{src_dtype}" '
                     'BlockXSize="256" BlockYSize="256"/>')
        lines.append('      <SrcRect xOff="0" yOff="0" '
                     f'xSize="{t["width"]}" ySize="{t["height"]}"/>')
        lines.append(f'      <DstRect xOff="{t["col0"]}" yOff="{t["row0"]}" '
                     f'xSize="{t["width"]}" ySize="{t["height"]}"/>')
        if nodata is not None:
            lines.append(f'      <NODATA>{nodata}</NODATA>')
        if lut:
            lines.append(f'      <LUT>{lut}</LUT>')
        lines.append('    </ComplexSource>')

    for band, dtype, desc, band_nodata, source_nodata, override in _BANDS:
        lines.append(f'  <VRTRasterBand dataType="{dtype}" band="{band}">')
        lines.append(f'    <Description>{desc}</Description>')
        if band_nodata is not None:
            lines.append(f'    <NoDataValue>{band_nodata}</NoDataValue>')
        for t in tiles:
            emit(t, band, dtype, source_nodata)
        if override:
            # Painted after every plain source, so it wins wherever it applies at all.
            ov_band, ov_dtype, ov_nodata, ov_lut = override
            for t in tiles:
                emit(t, ov_band, ov_dtype, ov_nodata, ov_lut)
        lines.append('  </VRTRasterBand>')
    lines.append('</VRTDataset>')
    # QGIS holds the VRT open while it reads it, so on Windows os.replace can lose a race with
    # ACCESS DENIED. Retry briefly, and if it never wins just skip this refresh — the next tile
    # will rewrite it. This MUST NOT raise: it runs per tile and an exception would kill the job.
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    for _ in range(20):
        try:
            os.replace(tmp, path)
            return path
        except PermissionError:
            time.sleep(0.05)
    try:
        os.remove(tmp)
    except OSError:
        pass
    return path

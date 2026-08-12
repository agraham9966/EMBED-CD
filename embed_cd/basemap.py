"""Year-matched Sentinel-2 photography, clipped to the area you are working on.

Ground-truthing a change map means flipping between "what did this look like in year A" and
"...in year B". EOX publishes cloudless Sentinel-2 mosaics per year as XYZ tiles, which QGIS can
add directly — but only as a GLOBAL layer that covers the whole canvas.

So this fetches instead of streams: GDAL reads the same tile service through its WMS/TMS driver,
`gdal.Translate` clips to the area and writes a plain GeoTIFF. That costs a download once, and
buys a layer that stops at the edge of your area (so the change map and the basemap outside it
stay visible), renders instantly afterwards, and still works offline.

EOX years are not AlphaEarth years — there is no 2017 mosaic — so `nearest_year` snaps and the
caller is expected to say so rather than silently showing the wrong year's imagery.
"""
import hashlib
import os

# What EOX actually publishes. AlphaEarth runs 2017-2025; 2017 has no matching mosaic.
EOX_YEARS = (2016, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025)
_URL = ("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-{year}_3857"
        "/default/g/${z}/${y}/${x}.jpg")
_UA = "embed-cd (QGIS plugin)"
MAX_PX = 4096          # caps both the file size and, since tiles follow resolution, the download


def nearest_year(year):
    """The EOX mosaic closest to an AlphaEarth year. Ties go to the earlier year, which is the
    conservative choice for a BEFORE image."""
    year = int(year)
    return year if year in EOX_YEARS else min(EOX_YEARS, key=lambda y: (abs(y - year), y))


def _wms_xml(year):
    """GDAL's description of an XYZ tile service. YOrigin=top because these are WMTS rows
    (numbered from the north), not TMS rows — getting this backwards returns a vertically
    mirrored world rather than an error."""
    # str.replace, not .format — the URL contains GDAL's own ${x}/${y}/${z} placeholders and
    # .format() reads those braces as fields of its own.
    url = _URL.replace("{year}", str(year))
    return f"""<GDAL_WMS>
  <Service name="TMS">
    <ServerUrl>{url}</ServerUrl>
  </Service>
  <DataWindow>
    <UpperLeftX>-20037508.34</UpperLeftX>
    <UpperLeftY>20037508.34</UpperLeftY>
    <LowerRightX>20037508.34</LowerRightX>
    <LowerRightY>-20037508.34</LowerRightY>
    <TileLevel>18</TileLevel>
    <TileCountX>1</TileCountX>
    <TileCountY>1</TileCountY>
    <YOrigin>top</YOrigin>
  </DataWindow>
  <Projection>EPSG:3857</Projection>
  <BlockSizeX>256</BlockSizeX>
  <BlockSizeY>256</BlockSizeY>
  <BandsCount>3</BandsCount>
  <UserAgent>{_UA}</UserAgent>
  <Timeout>30</Timeout>
  <ZeroBlockHttpCodes>204,404</ZeroBlockHttpCodes>
</GDAL_WMS>"""


def photo_filename(bbox_4326, year):
    """Keyed by area AND year, so toggling between two years is a disk hit after the first fetch
    and re-drawing the same area later costs nothing."""
    key = hashlib.sha1(("%.5f_%.5f_%.5f_%.5f" % tuple(bbox_4326)).encode()).hexdigest()[:10]
    return f"photo_{year}_{key}.tif"


def size_for(bbox_4326, max_px=MAX_PX):
    """(width, height, metres per pixel). Aims at 10 m to match the embeddings, but never
    exceeds max_px on a side — a whole-island area would otherwise try to pull a country's worth
    of tiles."""
    import math

    lo, la, hi, ha = bbox_4326
    w_m = (hi - lo) * 111320.0 * math.cos(math.radians((la + ha) / 2))
    h_m = (ha - la) * 110570.0
    res = max(10.0, max(w_m, h_m) / max_px)
    return max(1, int(w_m / res)), max(1, int(h_m / res)), res


def fetch(path, bbox_4326, year, max_px=MAX_PX, callback=None):
    """Write a clipped GeoTIFF of the year's mosaic. Returns the path, or the cached one.

    `callback` is passed straight to GDAL, which calls it as (fraction, message, data) — enough
    to drive a progress dialog and, by returning 0, to cancel a slow fetch.
    """
    from osgeo import gdal

    gdal.UseExceptions()
    if os.path.exists(path):
        return path
    w, h, _res = size_for(bbox_4326, max_px)
    lo, la, hi, ha = bbox_4326
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    try:
        # format is explicit because the ".part" suffix gives GDAL no extension to infer from
        gdal.Translate(tmp, _wms_xml(year), format="GTiff", width=w, height=h,
                       projWin=[lo, ha, hi, la], projWinSRS="EPSG:4326",
                       creationOptions=["COMPRESS=JPEG", "TILED=YES", "PHOTOMETRIC=YCBCR"],
                       callback=callback)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    os.replace(tmp, path)
    return path

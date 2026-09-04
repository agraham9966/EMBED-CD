"""Year-matched Sentinel-2 photography, streamed as XYZ tiles.

Ground-truthing a change map means asking "what did this actually look like in year X" — and,
when several years are loaded at once, "which year did it appear in". EOX publishes cloudless
Sentinel-2 mosaics per year as tiles, which QGIS streams natively: nothing is downloaded, tiles
arrive as you pan, and any number of years can sit in the layer tree to be toggled against each
other.

What the imagery IS, since it is easy to over-read: an annual COMPOSITE. Every Sentinel-2
acquisition over the calendar year is cloud/shadow/snow-masked, BRDF-corrected and merged
per-pixel into a synthetic cloud-free image. No pixel corresponds to a single moment, so it
answers "what was here that year", never "what date did this change".

LICENSING — read before reusing this anywhere. The 2018+ mosaics are CC BY-NC-SA 4.0:
NON-COMMERCIAL, share-alike, attribution required. Only 2016 is plain CC BY 4.0. That is why
`ATTRIBUTION` exists and why the UI shows it rather than tucking it in a tooltip; a commercial
user needs to know before they put this in a deliverable. Commercial licences: cloudless.eox.at
"""

# What EOX actually publishes. AlphaEarth runs 2017-2025; there is no 2017 mosaic.
EOX_YEARS = (2016, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025)
_TEMPLATE = ("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-{year}_3857"
             "/default/g/{{z}}/{{y}}/{{x}}.jpg")
ZMAX = 18

ATTRIBUTION = ("Sentinel-2 cloudless — https://s2maps.eu by EOX IT Services GmbH "
               "(Contains modified Copernicus Sentinel data)")
LICENCE = "CC BY-NC-SA 4.0 — non-commercial use only (2016: CC BY 4.0)"


def nearest_year(year):
    """The EOX mosaic closest to an AlphaEarth year. Ties go to the earlier year, the
    conservative choice when the imagery is standing in for a BEFORE state."""
    year = int(year)
    return year if year in EOX_YEARS else min(EOX_YEARS, key=lambda y: (abs(y - year), y))


def xyz_uri(year, zmax=ZMAX):
    """QGIS data-source URI for one year's tiles, for the `wms` provider.

    The template's own {z}/{y}/{x} are doubled in `_TEMPLATE` so .format() leaves them alone —
    they are QGIS's placeholders, not ours. Note the path order is z/y/x: these are WMTS rows
    and columns, not the z/x/y most XYZ services use, and swapping them silently returns the
    wrong part of the world rather than an error.

    Only the {z}/{y}/{x} braces are percent-encoded — NOT the scheme or the slashes. That is
    exactly how QGIS serializes a manual XYZ connection, and it has to match: `quote(url,
    safe="")` encoded the "://" and every "/" too, so the provider requested a literally
    mangled host (https%3A%2F%2Ftiles.maps.eox.at%2F...), which failed every tile with a
    "max retry" while the identical URL added by hand worked. The URL carries no "&" or "="
    of its own, so it needs no other escaping to sit safely in the datasource string.
    """
    url = _TEMPLATE.format(year=int(year))
    url = url.replace("{", "%7B").replace("}", "%7D")
    return f"type=xyz&url={url}&zmax={zmax}&zmin=0"


def layer_name(year):
    """EOX's attribution has to be legible and near the imagery; the legend entry is the one
    place a user cannot miss it."""
    return f"Sentinel-2 cloudless {year} (EOX)"

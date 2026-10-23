// Year-matched Sentinel-2 cloudless imagery from EOX, streamed as tiles (embed_cd/basemap.py).
// Annual composites: "what was here that year", never "on what date". Licence: the 2018+ mosaics
// are CC BY-NC-SA 4.0 (non-commercial), 2016 is CC BY 4.0, so the credit is shown, not hidden.

/** What EOX publishes. AlphaEarth runs 2017-2025; there is no 2017 mosaic. */
export const EOX_YEARS = [2016, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025];
export const ZMAX = 18;
export const ATTRIBUTION = "Sentinel-2 cloudless, https://s2maps.eu by EOX IT Services GmbH (Contains modified Copernicus Sentinel data)";
export const LICENCE = "CC BY-NC-SA 4.0, non-commercial use only (2016: CC BY 4.0)";

/** The EOX year closest to an AlphaEarth year; ties go to the earlier (safer as a "before"). */
export function nearestYear(year: number): number {
  if (EOX_YEARS.includes(year)) return year;
  return EOX_YEARS.reduce((best, y) => (Math.abs(y - year) < Math.abs(best - year) ? y : best));
}

/** Tile template. The path is z/y/x (WMTS row then column): swapping them shows the wrong place.
 *  EOX names the 2016 mosaic plain "s2cloudless" ("s2cloudless-2016" is a 404). */
export const tileUrl = (year: number) =>
  `https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless${year === 2016 ? "" : `-${year}`}_3857/default/g/{z}/{y}/{x}.jpg`;

export const layerName = (year: number) => `Sentinel-2 cloudless ${year} (EOX)`;

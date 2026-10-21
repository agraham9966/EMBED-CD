import { fromArrayBuffer } from "geotiff";
import { makeTestGeoTiff } from "../src/spike";

test("the test GeoTIFF is a 64x64 float in UTM 10N at 500000, 5500000", async () => {
  const blob = makeTestGeoTiff();
  const img = await (await fromArrayBuffer(await blob.arrayBuffer())).getImage();
  expect([img.getWidth(), img.getHeight()]).toEqual([64, 64]);
  expect(img.getGeoKeys()?.ProjectedCSTypeGeoKey).toBe(32610);
  expect(img.getOrigin().slice(0, 2)).toEqual([500000, 5500000]);
  expect(img.getResolution().slice(0, 2)).toEqual([10, -10]);
  const [band] = (await img.readRasters()) as unknown as Float32Array[];
  expect(band[63 * 64]).toBeCloseTo(1);
});

import { objectStyle, ObjectsLayer } from "../src/map/objectslayer";

test("fill and outline follow the class colours, unknown is white, unclassified faint (QGIS _style)", () => {
  const s = objectStyle({ cutblock: "#d85a30", road: "#1d9e75" });
  expect(s.fill).toEqual(["match", ["get", "predicted"],
    "cutblock", "rgba(216,90,48,0.431)", "road", "rgba(29,158,117,0.431)",
    "unknown", "rgba(255,255,255,0.118)", "rgba(255,255,255,0.086)"]);
  expect(s.lineColor).toEqual(["match", ["get", "predicted"],
    "cutblock", "rgb(216,90,48)", "road", "rgb(29,158,117)", "unknown", "rgb(255,255,255)", "rgba(235,235,235,0.863)"]);
  expect(s.lineWidth).toEqual(["match", ["get", "predicted"], "cutblock", 3, "road", 3, "unknown", 3.8, 2.6]);
});

test("labels and predictions are written onto the objects; the current one is highlighted", () => {
  const layers: any[] = [];
  let data: any = null;
  const src = { setData: (d: any) => { data = d; } };
  const map: any = { style: { _loaded: true }, on() {}, off() {}, getSource: (id: string) => (layers.length ? src : undefined),
    addSource: (_: string, s: any) => { data = s.data; }, addLayer: (l: any) => layers.push(l), getLayer: () => true,
    setPaintProperty() {}, setFilter: (id: string, f: any) => { layers.find((l) => l.id === id).filter = f; } };
  const ring: [number, number][] = [[500000, 5500000], [500000, 5499990], [500010, 5499990], [500010, 5500000], [500000, 5500000]];
  const poly = { rings: [ring], areaHa: 0.01, chgMean: 0.4, chgMax: 0.5, nPx: 1 };
  const L = new ObjectsLayer({ getMap: () => map } as any, "r", "x");
  L.set([poly, poly], { crs: "EPSG:32610", x0: 0, y0: 0, res: 10, width: 1, height: 1 });
  L.update(["cutblock", "unknown"], new Map([[0, "cutblock"]]), { cutblock: "#d85a30" }, Float64Array.from([0.9, 0.4]));
  expect(data.features.map((f: any) => [f.properties.predicted, f.properties.label, f.properties.confidence])).toEqual(
    [["cutblock", "cutblock", 0.9], ["unknown", "", 0.4]]);
  expect(layers.map((l) => l.id)).toEqual(["ecd-r-objects-fill", "ecd-r-objects-line", "ecd-r-objects-labelled", "ecd-r-objects-current"]);
  expect(layers[1].filter).toEqual(["==", ["get", "label"], ""]);
  expect(layers[2].filter).toEqual(["!=", ["get", "label"], ""]);
  expect(layers[2].paint["line-dasharray"]).toBeTruthy();
  L.highlight(1);
  expect(layers[3].filter).toEqual(["==", ["get", "id"], 2]);
});

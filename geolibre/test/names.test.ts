import { autoName, groupName, resolveGroup, runLabel } from "../src/runs";

test("an unnamed area is called by its centre, as in QGIS", () => {
  expect(autoName([-125.34, 49.63, -125.24, 49.71])).toBe("49.67N 125.29W");
  expect(autoName([10, -30, 12, -28])).toBe("29.00S 11.00E");
});

test("groups are '<name>  <A>→<B>', made unique with (2), (3)", () => {
  expect(groupName("", "49.67N 125.29W", 2019, 2024)).toBe("49.67N 125.29W  2019→2024");
  expect(groupName("Nanaimo", "x", 2019, 2024)).toBe("Nanaimo  2019→2024");
  expect(resolveGroup("Nanaimo  2019→2024", [])).toBe("Nanaimo  2019→2024");
  expect(resolveGroup("Nanaimo  2019→2024", ["Nanaimo  2019→2024"])).toBe("Nanaimo  2019→2024 (2)");
  expect(resolveGroup("Nanaimo  2019→2024", ["Nanaimo  2019→2024", "Nanaimo  2019→2024 (2)"])).toBe("Nanaimo  2019→2024 (3)");
});

test("the Area list names a run by its name (or place), years and Detail", () => {
  const r: any = { userName: "", bbox: [-125.34, 49.63, -125.24, 49.71], yearA: 2019, yearB: 2024, resM: 10, complete: true };
  expect(runLabel(r)).toBe("49.67N 125.29W · 2019→2024 · 10 m (full)");
  expect(runLabel({ ...r, userName: "Nanaimo", resM: 50, complete: false })).toBe("Nanaimo · 2019→2024 · 50 m (incomplete)");
});

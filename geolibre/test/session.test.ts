import "fake-indexeddb/auto";
import { SessionRuns } from "../src/runs";

test("session runs live in memory, newest first, and touch no storage", async () => {
  const s = new SessionRuns();
  s.start({ id: "a", name: "A", createdAt: 1 } as any);
  s.start({ id: "b", name: "B", createdAt: 2 } as any);
  s.addTile("a", { window: [0, 1, 0, 1] } as any);
  expect(s.list().map((r) => r.id)).toEqual(["b", "a"]);
  expect(s.get("a")!.tiles.length).toBe(1);
  expect(await indexedDB.databases()).toEqual([]);
});


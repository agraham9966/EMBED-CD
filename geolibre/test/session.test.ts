import "fake-indexeddb/auto";
import { SessionRuns } from "../src/runs";
import { wipeLegacyStorage } from "../src/legacy";

test("session runs live in memory, newest first, and touch no storage", async () => {
  const s = new SessionRuns();
  s.start({ id: "a", name: "A", createdAt: 1 } as any);
  s.start({ id: "b", name: "B", createdAt: 2 } as any);
  s.addTile("a", { window: [0, 1, 0, 1] } as any);
  expect(s.list().map((r) => r.id)).toEqual(["b", "a"]);
  expect(s.get("a")!.tiles.length).toBe(1);
  expect(await indexedDB.databases()).toEqual([]);
});

test("the one-time cleanup deletes what earlier test builds stored", async () => {
  await new Promise<void>((r) => { const q = indexedDB.open("embed-cd", 1); q.onupgradeneeded = () => q.result.createObjectStore("runs"); q.onsuccess = () => { q.result.close(); r(); }; });
  expect((await indexedDB.databases()).map((d) => d.name)).toContain("embed-cd");
  await wipeLegacyStorage();
  expect((await indexedDB.databases()).map((d) => d.name)).not.toContain("embed-cd");
});

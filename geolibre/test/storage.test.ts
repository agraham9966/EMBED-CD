import "fake-indexeddb/auto";
import { openRunStore, StorageUnavailableError, describeStorageError } from "../src/storage";

test("a run and its blobs round-trip and list newest first", async () => {
  const s = await openRunStore("t1");
  await s.putRun({ id: "a", name: "A", createdAt: 1, bytes: 3 });
  await s.putRun({ id: "b", name: "B", createdAt: 2, bytes: 0 });
  await s.putBlob("a", "score.bin", new Blob([new Uint8Array([1, 2, 3])]));
  expect((await s.listRuns()).map((r) => r.id)).toEqual(["b", "a"]);
  const got = await s.getBlob("a", "score.bin");
  expect(new Uint8Array(await got!.arrayBuffer())).toEqual(new Uint8Array([1, 2, 3]));
});

test("deleteRun removes its blobs", async () => {
  const s = await openRunStore("t2");
  await s.putRun({ id: "a", name: "A", createdAt: 1, bytes: 1 });
  await s.putBlob("a", "x", new Blob(["1"]));
  await s.deleteRun("a");
  expect(await s.getBlob("a", "x")).toBeNull();
  expect(await s.listRuns()).toEqual([]);
});

test("no IndexedDB gives StorageUnavailableError and a plain message", async () => {
  const saved = globalThis.indexedDB;
  (globalThis as any).indexedDB = undefined;
  try {
    await expect(openRunStore("t3")).rejects.toBeInstanceOf(StorageUnavailableError);
  } finally {
    (globalThis as any).indexedDB = saved;
  }
  expect(describeStorageError(new StorageUnavailableError("x"))).toMatch(/can't be saved in this browser/);
});

test("quota errors read as plain words", () => {
  const e = new DOMException("full", "QuotaExceededError");
  expect(describeStorageError(e)).toMatch(/Not enough storage space/);
});

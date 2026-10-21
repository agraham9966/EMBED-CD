// Runs kept in the app's own storage (IndexedDB), since a plugin cannot write folders on disk.
// Two stores: `runs` (one record per run) and `blobs` (the run's files, keyed [runId, name]).

export type RunMeta = { id: string; name: string; createdAt: number; bytes: number };

export class StorageUnavailableError extends Error {}

export interface RunStore {
  putRun(meta: RunMeta): Promise<void>;
  listRuns(): Promise<RunMeta[]>;
  putBlob(runId: string, name: string, data: Blob): Promise<void>;
  getBlob(runId: string, name: string): Promise<Blob | null>;
  deleteRun(runId: string): Promise<void>;
  usage(): Promise<{ used: number; quota: number } | null>;
  persist(): Promise<boolean>;
}

function done<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function committed(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

export async function openRunStore(dbName = "embed-cd"): Promise<RunStore> {
  const idb: IDBFactory | undefined = globalThis.indexedDB;
  if (!idb) throw new StorageUnavailableError("IndexedDB is not available");
  let db: IDBDatabase;
  try {
    const req = idb.open(dbName, 1);
    req.onupgradeneeded = () => {
      req.result.createObjectStore("runs", { keyPath: "id" });
      req.result.createObjectStore("blobs");
    };
    db = await done(req);
  } catch (e) {
    throw new StorageUnavailableError(String((e as Error)?.message ?? e));
  }

  const write = (stores: string[], fn: (tx: IDBTransaction) => void) => {
    const tx = db.transaction(stores, "readwrite");
    fn(tx);
    return committed(tx);
  };

  return {
    putRun: (meta) => write(["runs"], (tx) => tx.objectStore("runs").put(meta)),
    async listRuns() {
      const all = (await done(db.transaction("runs").objectStore("runs").getAll())) as RunMeta[];
      return all.sort((a, b) => b.createdAt - a.createdAt);
    },
    // Raw bytes, not the Blob itself: Blobs in IndexedDB have a history of breaking on WebKit
    // (GeoLibre desktop's engine on macOS and Linux), and bytes clone the same everywhere.
    async putBlob(runId, name, data) {
      const bytes = await data.arrayBuffer();
      await write(["blobs"], (tx) => tx.objectStore("blobs").put(bytes, [runId, name]));
    },
    async getBlob(runId, name) {
      const got = await done(db.transaction("blobs").objectStore("blobs").get([runId, name]));
      return got === undefined ? null : new Blob([got as ArrayBuffer]);
    },
    deleteRun: (runId) =>
      write(["runs", "blobs"], (tx) => {
        tx.objectStore("runs").delete(runId);
        // every [runId, name] key sorts between [runId] and [runId, []]
        tx.objectStore("blobs").delete(IDBKeyRange.bound([runId], [runId, []]));
      }),
    async usage() {
      const est = await globalThis.navigator?.storage?.estimate?.();
      return est ? { used: est.usage ?? 0, quota: est.quota ?? 0 } : null;
    },
    async persist() {
      return (await globalThis.navigator?.storage?.persist?.()) ?? false;
    },
  };
}

export function describeStorageError(e: unknown): string {
  if (e instanceof StorageUnavailableError) {
    return "Runs can't be saved in this browser (storage is blocked or private browsing is on).";
  }
  if ((e as { name?: string })?.name === "QuotaExceededError") {
    return "Not enough storage space for this run. Delete an old run or use a coarser Detail.";
  }
  return `Saving failed: ${(e as Error)?.message ?? String(e)}`;
}

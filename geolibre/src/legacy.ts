// Builds 0.1 and 0.2 kept runs (and a 20 MB storage test) in IndexedDB. Runs are session-only
// now, so delete that database once: nothing of ours should sit on disk without the user knowing.
export function wipeLegacyStorage(): Promise<void> {
  return new Promise((resolve) => {
    const idb: IDBFactory | undefined = globalThis.indexedDB;
    if (!idb) return resolve();
    const req = idb.deleteDatabase("embed-cd");
    req.onsuccess = req.onerror = req.onblocked = () => resolve();
  });
}

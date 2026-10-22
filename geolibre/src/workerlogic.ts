// What the job worker does, separate from the Worker wiring so tests can run it in-process.
import { fromUrl, type GeoTIFF } from "geotiff";
import { runJob, type JobEvent, type JobSpec } from "./engine/job";
import { TileIndex } from "./engine/tileindex";

export type WorkerIn = { type: "start"; spec: JobSpec } | { type: "cancel" };
export type WorkerOut = JobEvent | { type: "error"; message: string };
export type WorkerDeps = {
  loadIndex: () => Promise<TileIndex>;
  open: (url: string) => Promise<GeoTIFF>;
  tilePx?: number;
};

const defaultDeps: WorkerDeps = { loadIndex: () => TileIndex.load(), open: (url) => fromUrl(url) };

export function createHandler(post: (msg: WorkerOut, transfer?: Transferable[]) => void, deps = defaultDeps) {
  let index: Promise<TileIndex> | null = null;     // kept for the worker's life: 4 MB, once
  let abort: AbortController | null = null;
  return async (msg: WorkerIn) => {
    if (msg.type === "cancel") {
      abort?.abort();
      return;
    }
    abort = new AbortController();
    const signal = abort.signal;
    try {
      index ??= deps.loadIndex();
      const idx = await index.catch((e) => { index = null; throw e; });
      // Each COG is opened once per job: both years and every tile in it share the header read.
      const opened = new Map<string, Promise<GeoTIFF>>();
      const open = (url: string) => {
        let p = opened.get(url);
        if (!p) { p = deps.open(url); opened.set(url, p); p.catch(() => opened.delete(url)); }
        return p;
      };
      for await (const e of runJob(msg.spec, { index: idx, open, tilePx: deps.tilePx }, signal)) {
        if (e.type === "tile") {
          const c = e.result.cells;
          post(e, [e.result.score.buffer, e.result.cov.buffer, e.result.hist.buffer,
            ...(c ? [c.ma.buffer, c.mb.buffer, c.n.buffer, c.smean.buffer, c.smax.buffer] : [])]);
        }
        else post(e);
      }
    } catch (e) {
      post({ type: "error", message: String((e as Error)?.message ?? e) });
    }
  };
}

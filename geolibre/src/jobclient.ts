// Starts and cancels a change job running in the Web Worker.
import type { JobSpec } from "./engine/job";
import { HIST_BINS } from "./engine/score";
import type { WorkerOut } from "./workerlogic";
import JobWorker from "./worker?worker&inline";

export type ClientEvent = WorkerOut;

export function startJob(
  spec: JobSpec,
  onEvent: (e: ClientEvent) => void,
  createWorker: () => Worker = () => new JobWorker(),
): { cancel(): void; done: Promise<void> } {
  const worker = createWorker();
  let finish!: () => void;
  const done = new Promise<void>((r) => (finish = r));
  let stopped = false;
  const hist = new Float64Array(HIST_BINS);        // of the tiles delivered, for a cancelled end
  const stop = () => { stopped = true; worker.terminate(); finish(); };
  worker.onmessage = (e: MessageEvent<ClientEvent>) => {
    if (stopped) return;                           // sent before a cancel, still in the queue
    if (e.data.type === "tile") for (let i = 0; i < HIST_BINS; i++) hist[i] += e.data.result.hist[i] ?? 0;
    onEvent(e.data);
    if (e.data.type === "end" || e.data.type === "error") stop();
  };
  worker.onerror = (e) => {
    if (stopped) return;
    onEvent({ type: "error", message: e.message || "The background job stopped unexpectedly." });
    stop();
  };
  worker.postMessage({ type: "start", spec });
  // Cancel stops at once, not after the tile in progress: the worker is shut down and any tiles
  // it had already sent are dropped. Drawing a backlog of them one by one is what made Cancel
  // look ignored on a big run.
  const cancel = () => {
    if (stopped) return;
    stop();
    onEvent({ type: "end", histTotal: hist, failed: 0, cancelled: true });
  };
  return { cancel, done };
}

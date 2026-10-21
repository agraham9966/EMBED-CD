// Starts and cancels a change job running in the Web Worker.
import type { JobSpec } from "./engine/job";
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
  worker.onmessage = (e: MessageEvent<ClientEvent>) => {
    onEvent(e.data);
    if (e.data.type === "end" || e.data.type === "error") {
      worker.terminate();
      finish();
    }
  };
  worker.onerror = (e) => {
    onEvent({ type: "error", message: e.message || "The background job stopped unexpectedly." });
    worker.terminate();
    finish();
  };
  worker.postMessage({ type: "start", spec });
  return { cancel: () => worker.postMessage({ type: "cancel" }), done };
}

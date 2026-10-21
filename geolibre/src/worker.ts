// The job's Web Worker. Built inline (a blob: URL), which the desktop app's CSP allows.
import { createHandler, type WorkerIn } from "./workerlogic";

const handle = createHandler((msg, transfer) => (self as unknown as Worker).postMessage(msg, transfer ?? []));
self.onmessage = (e: MessageEvent<WorkerIn>) => void handle(e.data);

// numpy .npz reader: a zip of .npy files. Inflate uses the browser's own DecompressionStream,
// so the tile index needs no zip library.

export type NpyArray = {
  dtype: string;
  shape: number[];
  data: Float64Array | Int32Array | Uint8Array;
  itemsize: number;
};

async function inflateRaw(bytes: Uint8Array): Promise<Uint8Array> {
  const input = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(bytes);
      c.close();
    },
  });
  const stream = input.pipeThrough(new DecompressionStream("deflate-raw") as unknown as ReadableWritablePair<Uint8Array, Uint8Array>);
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

function parseNpy(buf: Uint8Array): NpyArray {
  const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  if (buf[0] !== 0x93 || String.fromCharCode(...buf.subarray(1, 6)) !== "NUMPY") {
    throw new Error("not an .npy file");
  }
  const major = buf[6];
  const headerLen = major === 1 ? view.getUint16(8, true) : view.getUint32(8, true);
  const start = major === 1 ? 10 : 12;
  const header = new TextDecoder().decode(buf.subarray(start, start + headerLen));
  const descr = /'descr':\s*'([^']+)'/.exec(header)![1];
  const shape = /'shape':\s*\(([^)]*)\)/.exec(header)![1]
    .split(",").map((s) => s.trim()).filter(Boolean).map(Number);
  if (/'fortran_order':\s*True/.test(header)) throw new Error("fortran-order arrays are not supported");
  const body = buf.slice(start + headerLen);           // copy: aligned and owned
  const n = shape.reduce((a, b) => a * b, 1);
  if (descr === "<f8") return { dtype: descr, shape, data: new Float64Array(body.buffer, 0, n), itemsize: 8 };
  if (descr === "<i4") return { dtype: descr, shape, data: new Int32Array(body.buffer, 0, n), itemsize: 4 };
  const s = /^\|S(\d+)$/.exec(descr);
  if (s) return { dtype: descr, shape, data: body, itemsize: Number(s[1]) };
  throw new Error(`unsupported npy dtype ${descr}`);
}

/** Every file in a zip (stored or deflated), keyed by name. */
export async function readZip(ab: ArrayBuffer): Promise<Record<string, Uint8Array>> {
  const buf = new Uint8Array(ab);
  const view = new DataView(ab);
  let eocd = buf.length - 22;
  while (eocd >= 0 && view.getUint32(eocd, true) !== 0x06054b50) eocd--;
  if (eocd < 0) throw new Error("not a zip file");
  const count = view.getUint16(eocd + 10, true);
  let p = view.getUint32(eocd + 16, true);
  const out: Record<string, Uint8Array> = {};
  for (let i = 0; i < count; i++) {
    const method = view.getUint16(p + 10, true);
    const csize = view.getUint32(p + 20, true);
    const nameLen = view.getUint16(p + 28, true);
    const extraLen = view.getUint16(p + 30, true);
    const commentLen = view.getUint16(p + 32, true);
    const local = view.getUint32(p + 42, true);
    const name = new TextDecoder().decode(buf.subarray(p + 46, p + 46 + nameLen));
    p += 46 + nameLen + extraLen + commentLen;
    const dataAt = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
    const raw = buf.subarray(dataAt, dataAt + csize);
    const bytes = method === 0 ? raw : method === 8 ? await inflateRaw(raw) : null;
    if (!bytes) throw new Error(`unsupported zip method ${method} for ${name}`);
    out[name] = bytes;
  }
  return out;
}

/** Every array in an .npz, keyed by name (without ".npy"). */
export async function readNpz(ab: ArrayBuffer): Promise<Record<string, NpyArray>> {
  const out: Record<string, NpyArray> = {};
  for (const [name, bytes] of Object.entries(await readZip(ab))) out[name.replace(/\.npy$/, "")] = parseNpy(bytes);
  return out;
}

/** One fixed-width bytes (`|S`) element as a string, trailing NULs dropped. */
export function npyString(a: NpyArray, i: number): string {
  const b = (a.data as Uint8Array).subarray(i * a.itemsize, (i + 1) * a.itemsize);
  let end = b.indexOf(0);
  if (end < 0) end = b.length;
  return new TextDecoder().decode(b.subarray(0, end));
}

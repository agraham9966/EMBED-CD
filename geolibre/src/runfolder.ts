// Save to folder (the desktop app, GeoLibre 3.6.1 and later): a run is written as it goes, so what
// is finished survives a crash, as the QGIS plugin's run folder does. A host write costs the page
// ~80 ms per MB (it sends the bytes as JSON), so tiles are packed and deflated in the job worker,
// and go to the host in small parts, one at a time: never a long stall, and never a slower run.
// Each run gets a subfolder where GeoLibre can make one, and each file name carries the run's prefix
// anyway, so runs written side by side by an older GeoLibre read back the same:
//   <prefix>_run.json             what the run is (rewritten when it ends)
//   <prefix>_t0007_p1of3.part     tile 7, a zip cut into parts; all of them or it does not count
//   <prefix>_o<stamp>_p1of2.part  the objects from one Generate, cut the same way
//   <prefix>_labels.json          classes and labels (rewritten as you label)
import { type CellGrid } from "./engine/cells";
import type { TileResult } from "./engine/job";
import { readZip } from "./engine/npz";
import type { LabelState } from "./labeller";
import { writeZip } from "./runfile";
import type { RunObjects, RunRecord } from "./runs";

export const PART_BYTES = 2 * 1024 * 1024;   // at most ~200 ms of the page per part, measured
const FORMAT = "embed-cd-run-folder";
const LABEL_DELAY_MS = 1000;

const bytes = (v: ArrayBufferView) => new Uint8Array(v.buffer, v.byteOffset, v.byteLength);
const json = (v: unknown) => new TextEncoder().encode(JSON.stringify(v));
const pad = (n: number) => String(n).padStart(2, "0");

/** The run's name as part of a file name: letters, digits and dashes, short enough that the host
 *  takes the longest file made from it (120 characters). Never holds "_", which splits the parts. */
function nameSlug(name = ""): string {
  const slug = name.trim().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "");
  return [...slug].slice(0, 40).join("").replace(/-+$/, "");
}

/** The name all of a run's files start with: its name, then a time stamp, so two runs given the
 *  same name stay apart. Fixed once a run starts (RunRecord.prefix), as a run can be renamed. */
export function runPrefix(m: RunRecord): string {
  const d = new Date(m.createdAt);
  const day = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  const slug = nameSlug(m.userName);
  return `embed-cd_${slug ? `${slug}_` : ""}${m.yearA}-${m.yearB}_${m.resM}m_${day}_${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}`;
}

/** One tile as one deflated zip. Runs in the job worker, before the tile is posted. */
export async function packTile(t: TileResult): Promise<Uint8Array> {
  const c = t.cells;
  const zip = await writeZip({
    "tile.json": json({ window: t.window, cells: c ? { crs: c.crs, x0: c.x0, y0: c.y0, res: c.res, width: c.width, height: c.height } : null }),
    "score.bin": bytes(t.score), "cov.bin": bytes(t.cov), "hist.bin": bytes(t.hist),
    ...(c ? { "ma.bin": bytes(c.ma), "mb.bin": bytes(c.mb), "n.bin": bytes(c.n), "smean.bin": bytes(c.smean), "smax.bin": bytes(c.smax) } : {}),
  });
  return new Uint8Array(await zip.arrayBuffer());
}

export async function unpackTile(packed: Uint8Array): Promise<TileResult> {
  const f = await readZip(packed.slice().buffer);
  const buf = (name: string) => f[name].slice().buffer;
  const head = JSON.parse(new TextDecoder().decode(f["tile.json"]));
  const cells: CellGrid | undefined = head.cells ? {
    ...head.cells, ma: new Int16Array(buf("ma.bin")), mb: new Int16Array(buf("mb.bin")),
    n: new Float32Array(buf("n.bin")), smean: new Float32Array(buf("smean.bin")), smax: new Float32Array(buf("smax.bin")),
  } : undefined;
  return { window: head.window, score: new Float32Array(buf("score.bin")), cov: new Uint8Array(buf("cov.bin")),
    hist: new Float64Array(buf("hist.bin")), ...(cells ? { cells } : {}) };
}

/** Objects and their embeddings, in the same shape as the Save run zip. */
export async function packObjects(o: RunObjects): Promise<Uint8Array> {
  const zip = await writeZip({
    "objects.json": json({ polys: o.polys, cutoff: o.cutoff, minArea: o.minArea }),
    "vectors.bin": bytes(o.vectors),
  });
  return new Uint8Array(await zip.arrayBuffer());
}

async function unpackObjects(packed: Uint8Array): Promise<RunObjects> {
  const f = await readZip(packed.slice().buffer);
  return { ...JSON.parse(new TextDecoder().decode(f["objects.json"])), vectors: new Float32Array(f["vectors.bin"].slice().buffer) };
}

type Write = (name: string, data: string | Uint8Array) => Promise<unknown>;

/** Writes a run's files one at a time, in order. A write that fails stops the saving and says
 *  why, but never reaches the run: it carries on in memory, and Save run still works. */
export class FolderWriter {
  failed: string | null = null;
  /** Which object set the labels belong to: the stamp in that set's file names. */
  objectsStamp = 0;
  private last: Promise<void> = Promise.resolve();
  private labelTimer: ReturnType<typeof setTimeout> | null = null;
  private labelState: LabelState | null = null;

  constructor(private write: Write, private prefix: string, private onFail?: (why: string) => void) {}

  private queue(job: () => Promise<unknown>): void {
    this.last = this.last.then(async () => {
      if (this.failed) return;
      try {
        await job();
      } catch (e) {
        this.failed = String((e as Error)?.message ?? e);
        this.onFail?.(this.failed);
      }
    });
  }

  private async parts(stem: string, packed: Uint8Array): Promise<void> {
    const total = Math.max(1, Math.ceil(packed.length / PART_BYTES));
    for (let p = 0; p < total; p++) {
      await this.write(`${stem}_p${p + 1}of${total}.part`, packed.subarray(p * PART_BYTES, (p + 1) * PART_BYTES));
    }
  }

  manifest(meta: RunRecord): void {
    const doc = JSON.stringify({ format: FORMAT, version: 1, meta }, null, 1);
    this.queue(() => this.write(`${this.prefix}_run.json`, doc));
  }

  tile(n: number, packed: Uint8Array): void {
    this.queue(() => this.parts(`${this.prefix}_t${String(n).padStart(4, "0")}`, packed));
  }

  /** A new object set. The host cannot delete files, so each set has its own stamp and an older
   *  set just stays behind, unread. */
  objects(o: RunObjects): void {
    const stamp = (this.objectsStamp = Date.now());
    this.queue(async () => this.parts(`${this.prefix}_o${stamp}`, await packObjects(o)));
  }

  /** Every label lands here: written a moment after the clicking stops, not on each click. */
  labels(state: LabelState): void {
    this.labelState = state;
    this.labelTimer ??= setTimeout(() => {
      this.labelTimer = null;
      const doc = JSON.stringify({ objects: this.objectsStamp, state: this.labelState });
      this.queue(() => this.write(`${this.prefix}_labels.json`, doc));
    }, LABEL_DELAY_MS);
  }

  /** Everything queued so far has been written, or given up on. */
  done(): Promise<void> {
    return this.last;
  }
}

export type FolderRun = { meta: RunRecord; prefix: string; tiles: number; parts: Map<number, File[]>; objects?: File[]; objectsStamp?: number; labels?: File };

/** Whole sets of parts, by the number in their name: a set missing a part does not count. */
function wholeSets(files: File[], prefix: string, tag: string): Map<number, File[]> {
  const re = new RegExp(`_${tag}(\\d+)_p(\\d+)of(\\d+)\\.part$`);
  const sets = new Map<number, { total: number; list: File[] }>();
  for (const f of files) {
    const m = f.name.startsWith(`${prefix}_`) ? re.exec(f.name) : null;
    if (!m || m.index !== prefix.length) continue;
    const set = sets.get(Number(m[1])) ?? { total: Number(m[3]), list: [] };
    set.list[Number(m[2]) - 1] = f;
    sets.set(Number(m[1]), set);
  }
  const whole = [...sets].filter(([, t]) => Array.from({ length: t.total }, (_, i) => t.list[i]).every(Boolean));
  return new Map(whole.sort(([a], [b]) => a - b).map(([n, t]) => [n, t.list]));
}

/** The runs in a folder's files, newest first. */
export async function listFolderRuns(files: File[]): Promise<FolderRun[]> {
  const found: FolderRun[] = [];
  for (const f of files) {
    if (!f.name.endsWith("_run.json")) continue;
    let doc: { format?: string; meta?: RunRecord };
    try { doc = JSON.parse(await f.text()); } catch { continue; }
    if (doc.format !== FORMAT || !doc.meta) continue;
    const prefix = f.name.slice(0, -"_run.json".length);
    const parts = wholeSets(files, prefix, "t");
    const newest = [...wholeSets(files, prefix, "o")].at(-1);
    found.push({ meta: doc.meta, prefix, tiles: parts.size, parts, objects: newest?.[1], objectsStamp: newest?.[0],
      labels: files.find((l) => l.name === `${prefix}_labels.json`) });
  }
  return found.sort((a, b) => b.meta.createdAt - a.meta.createdAt);
}

const joined = async (list: File[]) => new Uint8Array(await new Blob(list).arrayBuffer());

export async function loadFolderRun(run: FolderRun): Promise<{
  meta: RunRecord; tiles: TileResult[]; objects?: RunObjects; labelState?: LabelState;
}> {
  const tiles: TileResult[] = [];
  for (const list of run.parts.values()) tiles.push(await unpackTile(await joined(list)));
  const objects = run.objects ? await unpackObjects(await joined(run.objects)) : undefined;
  let labelState: LabelState | undefined;
  if (run.labels) {
    try {
      const doc = JSON.parse(await run.labels.text()) as { objects: number; state: LabelState };
      // Labels point at rows of one object set. Against any other set, only the classes and
      // their examples still mean anything.
      labelState = doc.objects === run.objectsStamp ? doc.state : { ...doc.state, labels: [] };
    } catch { /* a run without its labels is still a run */ }
  }
  return { meta: { ...run.meta, prefix: run.prefix, tiles: tiles.length, groupId: undefined, objectsStamp: run.objectsStamp }, tiles, objects, labelState };
}

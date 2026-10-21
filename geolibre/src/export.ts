import type { GeoLibreAppAPI, GeoLibreFileDialogOptions } from "./host-api";

// How a file actually left the app. "download" can't be confirmed from code (a desktop webview
// may drop it silently), so the UI words it as "check your Downloads folder", never "saved".
export type SaveResult = { method: "picker" | "download" | "host" | "cancelled" };

function download(filename: string, data: Blob): SaveResult {
  const url = URL.createObjectURL(data);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  return { method: "download" };
}

/** Save binary data: a native Save dialog where the webview has one, else a download. */
export async function saveBinary(
  filename: string,
  data: Blob,
  accept: Record<string, string[]>,
): Promise<SaveResult> {
  const picker = (window as any).showSaveFilePicker;
  if (typeof picker === "function") {
    try {
      const handle = await picker({ suggestedName: filename, types: [{ accept }] });
      const out = await handle.createWritable();
      await out.write(data);
      await out.close();
      return { method: "picker" };
    } catch (e) {
      if ((e as { name?: string })?.name === "AbortError") return { method: "cancelled" };
      throw e;
    }
  }
  return download(filename, data);
}

/** Save text through GeoLibre's own helper (native dialog on desktop), else a download. */
export function saveText(
  app: GeoLibreAppAPI,
  filename: string,
  text: string,
  options?: GeoLibreFileDialogOptions,
): SaveResult {
  if (app.exportTextFile) {
    app.exportTextFile(filename, text, options);
    return { method: "host" };
  }
  return download(filename, new Blob([text], { type: options?.mimeType ?? "text/plain" }));
}

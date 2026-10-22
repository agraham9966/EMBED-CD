import { defineConfig } from "vitest/config";
import { resolve } from "node:path";

export default defineConfig({
  build: {
    lib: { entry: resolve(import.meta.dirname, "src/index.ts"), formats: ["es"], fileName: () => "index.js" },
    outDir: "plugin/dist",
    emptyOutDir: true,
    cssCodeSplit: false,
    minify: true,
    sourcemap: false,
    // Vite leaves ES library output unminified on purpose; the GeoLibre registry asks for minified.
    // ...and ONE file: GeoLibre imports the entry alone from a blob: URL, so geotiff.js's lazily
    // loaded decoders must be inlined, not split into chunks next to it.
    rolldownOptions: { output: { assetFileNames: () => "style.css", minify: true, codeSplitting: false } },
  },
  test: { environment: "jsdom", globals: true },
});

import { defineConfig } from "vitest/config";
import { resolve } from "node:path";

export default defineConfig({
  build: {
    lib: { entry: resolve(__dirname, "src/index.ts"), formats: ["es"], fileName: () => "index.js" },
    outDir: "plugin/dist",
    emptyOutDir: true,
    cssCodeSplit: false,
    minify: true,
    sourcemap: false,
    rollupOptions: { output: { assetFileNames: () => "style.css" } },
  },
  test: { environment: "jsdom", globals: true },
});

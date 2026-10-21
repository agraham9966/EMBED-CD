import { plugin } from "../src/index";
import manifest from "../plugin/plugin.json";

test("manifest and plugin agree", () => {
  expect([plugin.id, plugin.name, plugin.version]).toEqual([manifest.id, manifest.name, manifest.version]);
  expect(plugin.id).toBe("embed-cd");
  expect((plugin as any).activeByDefault).toBeUndefined();
});

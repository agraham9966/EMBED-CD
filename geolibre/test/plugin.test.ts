import { plugin } from "../src/index";
import manifest from "../plugin/plugin.json";

test("manifest and plugin agree", () => {
  expect([plugin.id, plugin.name, plugin.version]).toEqual([manifest.id, manifest.name, manifest.version]);
  expect(plugin.id).toBe("embed-cd");
  expect((plugin as any).activeByDefault).toBeUndefined();
});

test("activate twice registers one panel; deactivate removes it", async () => {
  const registered: string[] = [];
  const app = {
    registerRightPanel: vi.fn((p: { id: string }) => {
      registered.push(p.id);
      return () => registered.splice(registered.indexOf(p.id), 1);
    }),
    openRightPanel: vi.fn(),
    closeRightPanel: vi.fn(),
  } as any;
  await plugin.activate(app);
  await plugin.activate(app);
  expect(registered).toEqual(["embed-cd-panel"]);
  plugin.deactivate(app);
  expect(registered).toEqual([]);
});

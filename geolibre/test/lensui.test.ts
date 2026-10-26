import { buildLens } from "../src/ui/lensui";
import { CompareLens } from "../src/map/lens";

test("the lens shortcut (L by default, settable) toggles it, but not while typing, and not once the panel is gone", () => {
  const calls: boolean[] = [];
  const spy = vi.spyOn(CompareLens.prototype, "setActive").mockImplementation((on: boolean) => { calls.push(on); });
  const ui = buildLens({ getMap: () => null } as any);
  const lensB = [...ui.root.querySelectorAll("button")].find((b) => b.textContent?.startsWith("Before/after lens"))!;
  expect(lensB.textContent).toBe("Before/after lensL");               // the key shows on the button
  const keyBox = ui.root.querySelector(".ecd-key") as HTMLInputElement;
  document.body.append(ui.root);
  const press = (key: string, target: EventTarget = document.body, extra: KeyboardEventInit = {}) =>
    target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...extra }));
  press("l");
  expect(calls).toEqual([true]);
  press("l", document.body, { ctrlKey: true });          // Ctrl+L is the browser's, not ours
  keyBox.value = "a"; keyBox.dispatchEvent(new Event("input"));
  expect(keyBox.value).toBe("");                        // A is the lens's own after-year key
  keyBox.value = "k"; keyBox.dispatchEvent(new Event("input"));
  expect(keyBox.value).toBe("K");
  expect(lensB.textContent).toBe("Before/after lensK");
  press("k", keyBox);                                   // typing in a box is not a shortcut
  press("l");                                           // the old key does nothing now
  expect(calls).toEqual([true]);
  press("K");
  expect(calls).toEqual([true, true]);
  ui.dispose();
  calls.length = 0;
  press("k");                                           // gone with the panel
  expect(calls).toEqual([]);
  ui.root.remove();
  spy.mockRestore();
});

test("the lens sits in the Reference imagery section, above the year strip, not in step 3", async () => {
  const { registerPanel } = await import("../src/panel");
  let rendered: HTMLElement | null = null;
  const app: any = {
    getMap: () => null, registerRightPanel: (p: any) => { rendered = document.createElement("div"); p.render(rendered); return () => {}; },
    openRightPanel() {}, closeRightPanel() {},
  };
  registerPanel(app);
  const lens = [...rendered!.querySelectorAll("button")].find((b) => b.textContent?.startsWith("Before/after lens"))!;
  const section = lens.closest(".ecd-photos")!;
  expect(section.querySelector(".ecd-head")!.textContent).toBe("Reference imagery");
  expect(lens.compareDocumentPosition(section.querySelector(".ecd-photo-row")!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(lens.closest(".ecd-acc-body")).toBeNull();
});

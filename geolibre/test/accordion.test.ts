import { Accordion } from "../src/ui/accordion";

function make() {
  const a = new Accordion();
  for (const t of ["one", "two", "three"]) a.add(document.createElement("div"));
  a.setTitles(["1 · Area", "2 · Change map", "3 · Objects and classes"]);
  return a;
}
const heads = (a: Accordion) => [...a.root.querySelectorAll(".ecd-acc-head")] as HTMLButtonElement[];
const open = (a: Accordion) => [...a.root.querySelectorAll(".ecd-acc-body")].map((b) => !(b as HTMLElement).hidden);

test("one step open at a time, with the caret saying which", () => {
  const a = make();
  expect(open(a)).toEqual([true, false, false]);
  expect(heads(a).map((h) => h.textContent)).toEqual(["▾ 1 · Area", "▸ 2 · Change map", "▸ 3 · Objects and classes"]);
  heads(a)[1].click();
  expect(open(a)).toEqual([false, true, false]);
  expect(heads(a)[1].textContent).toBe("▾ 2 · Change map");
});

test("a step that cannot be used yet cannot be opened", () => {
  const a = make();
  a.setEnabled([true, false, false]);
  heads(a)[2].click();
  expect(open(a)).toEqual([true, false, false]);
  expect(heads(a)[2].disabled).toBe(true);
});

test("moving forward happens once, so going back on purpose is respected", () => {
  const a = make();
  a.foldOnce("step1", 1);
  expect(open(a)).toEqual([false, true, false]);
  heads(a)[0].click();
  a.foldOnce("step1", 1);                       // a later refresh must not drag you forward again
  expect(open(a)).toEqual([true, false, false]);
  a.resetFold("step1");                         // a new area: the next result moves you on again
  a.foldOnce("step1", 1);
  expect(open(a)).toEqual([false, true, false]);
});

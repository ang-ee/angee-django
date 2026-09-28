import { forwardRef } from "react";
import { describe, expect, test } from "vitest";

import { composeAddons, defineAddon } from "./define-addon";

const canonicalizer = { canonicalModelLabel: (name: string) => name };
const mark = () => null;
const brand = { name: "Notebook", mark: "notebook-mark" };

describe("addon brand ownership", () => {
  test("composes one brand whose icon is contributed by a later addon", () => {
    const composed = composeAddons([
      defineAddon({ id: "notebook", brand }),
      defineAddon({ id: "artwork", icons: { "notebook-mark": mark } }),
    ], canonicalizer);
    expect(composed.brand).toEqual(brand);
    expect(composed.icons[brand.mark]).toBe(mark);
  });

  test("returns a null brand when no addon claims identity", () => {
    expect(composeAddons([{ id: "notes" }], canonicalizer).brand).toBeNull();
  });

  test("refuses a second brand claim even when both values are identical", () => {
    expect(() => composeAddons([
      { id: "first", brand, icons: { "notebook-mark": mark } },
      { id: "second", brand },
    ], canonicalizer)).toThrow(/second.*brand/i);
  });

  test.each([undefined, "not-a-component", { arbitrary: true }])(
    "refuses an unrenderable mark registration: %j", (icon) => {
      expect(() => composeAddons([
        { id: "notebook", brand, icons: { "notebook-mark": icon } },
      ], canonicalizer)).toThrow(/notebook-mark.*not registered/);
    },
  );

  test("uses the icon registry's normalization and forwardRef component support", () => {
    const icon = forwardRef<SVGSVGElement>(() => null);
    expect(composeAddons([{
      id: "notebook", brand: { ...brand, mark: " NOTEBOOK-MARK " },
      icons: { "notebook-mark": icon },
    }], canonicalizer).brand?.mark).toBe(" NOTEBOOK-MARK ");
  });

  test.each([{ ...brand, name: " " }, { ...brand, mark: " " }])(
    "refuses empty identity fields: %j", (identity) => {
      expect(() => composeAddons([{ id: "empty", brand: identity }], canonicalizer))
        .toThrow(/empty.*brand name or mark/);
    },
  );
});

describe("addon translation ownership", () => {
  test.each([{}, { "unused.future.key": "Still reserved" }])(
    "refuses the entire ui namespace and names its claimant", (messages) => {
      expect(() => composeAddons([
        { id: "translation-claimant", i18n: { ui: messages } },
      ], canonicalizer)).toThrow(/translation-claimant.*reserved.*ui/);
    },
  );

  test("merges disjoint addon keys without mutating either bundle", () => {
    const first = Object.freeze({ notes: Object.freeze({ title: "Notes" }) });
    const second = Object.freeze({ notes: Object.freeze({ empty: "No notes" }), help: { title: "Help" } });
    expect(composeAddons([
      { id: "notes", i18n: first }, { id: "help", i18n: second },
    ], canonicalizer).i18n).toEqual({
      notes: { title: "Notes", empty: "No notes" }, help: { title: "Help" },
    });
    expect(first.notes).toEqual({ title: "Notes" });
    expect(second.notes).toEqual({ empty: "No notes" });
  });

  test("refuses duplicate translation keys rather than choosing the last addon", () => {
    expect(() => composeAddons([
      { id: "first", i18n: { notes: { title: "Notes" } } },
      { id: "second", i18n: { notes: { title: "Replacement" } } },
    ], canonicalizer)).toThrow(/second.*notes.title/);
  });
});

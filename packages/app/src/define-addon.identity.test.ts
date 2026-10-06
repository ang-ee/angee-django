import { describe, expect, test } from "vitest";

import { composeAddons, defineAddon } from "./define-addon";

const canonicalizer = { canonicalModelLabel: (name: string) => name };
const brand = { name: "Notebook", mark: "notebook-mark" };

describe("the deployment shell", () => {
  test("composes the deployment layer's ANGEE_UI.shell and nothing else's", () => {
    const shell = { brand, theme: "notebook.paper", hosts: { "notes.localhost": "notes" } };
    expect(composeAddons([{ id: "notes" }, { id: "deployment", dependsOn: ["notes"], shell }], canonicalizer).shell).toBe(shell);
    expect(composeAddons([{ id: "notes" }], canonicalizer).shell).toBeUndefined();
  });

  test("refuses a shell on an addon, whose home, brand and theme belong on its menu root", () => {
    expect(() => composeAddons([defineAddon({ id: "notebook", shell: { brand } })], canonicalizer))
      .toThrow('Addon "notebook" declares shell, which only the deployment\'s ANGEE_UI does; declare home, brand and theme on the addon\'s menu root.');
  });
});

describe("addon translation ownership", () => {
  test.each<Record<string, string>>([{}, { "unused.future.key": "Still reserved" }])(
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

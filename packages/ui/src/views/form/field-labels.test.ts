import { describe, expect, test } from "vitest";

import type { ReactNode } from "react";

import { resolveFieldLabels, resolveGroupLabels } from "./field-labels";

// Declared labels carry message keys as ReactNode until the surface resolves them.
const key = (namespace: string, name: string, fallback?: string): ReactNode =>
  ({ namespace, key: name, ...(fallback ? { fallback } : {}) }) as unknown as ReactNode;

const i18n = {
  getFixedT: (_language: null, namespace: string) => (key: string, options?: { defaultValue?: string }) =>
    namespace === "notes" && key === "field.title" ? "Titre" : options?.defaultValue,
};

describe("resolveFieldLabels", () => {
  test("translates message-key labels through the runtime, down to the templates, and keeps other labels", () => {
    const [field, plain] = resolveFieldLabels([
      {
        name: "title",
        label: key("notes", "field.title", "Title"),
        itemTemplate: { name: "item", label: key("notes", "field.missing", "Item") },
      },
      { name: "body", label: "Body" },
    ], i18n);
    expect(field?.label).toBe("Titre");
    expect(field?.itemTemplate?.label).toBe("Item");
    expect(plain?.label).toBe("Body");
  });

  test("a group's title resolves with its fields", () => {
    const [group] = resolveGroupLabels([{
      label: key("notes", "field.title"),
      fields: [{ name: "title", label: key("notes", "field.title") }],
      actions: [],
    }], null);
    expect(group?.label).toBe("field.title");
    expect(group?.fields[0]?.label).toBe("field.title");
  });
});

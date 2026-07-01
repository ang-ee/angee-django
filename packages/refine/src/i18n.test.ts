import { describe, expect, test } from "vitest";

import { createAngeeI18nProvider, translateAngeeMessage } from "./i18n";

const resources = {
  base: {
    "auth.signIn": "Sign in",
    greeting: "Hello {name}",
  },
  notes: {
    title: "Notes",
  },
};

describe("Angee Refine i18n provider", () => {
  test("resolves namespaced keys from merged runtime bundles", () => {
    expect(translateAngeeMessage(resources, "base.auth.signIn")).toBe("Sign in");
    expect(translateAngeeMessage(resources, "notes.title")).toBe("Notes");
  });

  test("preserves namespace fallback and interpolation", () => {
    expect(
      translateAngeeMessage(resources, "greeting", {
        namespace: "base",
        name: "Ada",
      }),
    ).toBe("Hello Ada");
  });

  test("falls back to default messages and then keys", () => {
    expect(
      translateAngeeMessage(resources, "missing.title", {}, "Untitled"),
    ).toBe("Untitled");
    expect(translateAngeeMessage(resources, "missing.title")).toBe("missing.title");
  });

  test("tracks Refine locale state", async () => {
    const provider = createAngeeI18nProvider(resources);

    expect(provider.getLocale()).toBe("en");
    await provider.changeLocale("fr");
    expect(provider.getLocale()).toBe("fr");
  });
});

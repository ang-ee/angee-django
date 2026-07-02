import { describe, expect, test } from "vitest";

import {
  createAngeeI18nProvider,
  createAngeeI18nRuntime,
} from "./i18n";

const resources = {
  ui: {
    "auth.signIn": "Sign in",
    greeting: "Hello {name}",
  },
  notes: {
    title: "Notes",
  },
};

describe("Angee app i18n runtime", () => {
  test("resolves namespace-relative keys from the single runtime instance", () => {
    const runtime = createAngeeI18nRuntime(resources);
    const uiT = runtime.instance.getFixedT(null, "ui");
    const notesT = runtime.instance.getFixedT(null, "notes");

    expect(uiT("auth.signIn")).toBe("Sign in");
    expect(notesT("title")).toBe("Notes");
  });

  test("preserves namespace fallback and interpolation", () => {
    const provider = createAngeeI18nProvider(resources);

    expect(provider.translate("greeting", { namespace: "ui", name: "Ada" })).toBe(
      "Hello Ada",
    );
  });

  test("falls back to default messages and then keys", () => {
    const provider = createAngeeI18nProvider(resources);

    expect(provider.translate("missing.title", {}, "Untitled")).toBe("Untitled");
    expect(provider.translate("missing.title")).toBe("missing.title");
  });

  test("tracks Refine locale state", async () => {
    const provider = createAngeeI18nProvider(resources);

    expect(provider.getLocale()).toBe("en");
    await provider.changeLocale("fr");
    expect(provider.getLocale()).toBe("fr");
  });
});

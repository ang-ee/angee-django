import { describe, expect, test } from "vitest";

import { createAngeeI18nRuntime } from "./i18n";

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
    const { provider } = createAngeeI18nRuntime(resources);

    expect(provider.translate("greeting", { namespace: "ui", name: "Ada" })).toBe(
      "Hello Ada",
    );
  });

  test("falls back to default messages and then keys", () => {
    const { provider } = createAngeeI18nRuntime(resources);

    expect(provider.translate("missing.title", {}, "Untitled")).toBe("Untitled");
    expect(provider.translate("missing.title")).toBe("missing.title");
  });

  test("reads a string second argument as refine's default message", () => {
    const { provider } = createAngeeI18nRuntime(resources);

    // refine's own `safeTranslate` calls `translate(key, defaultMessage)` when
    // it has no interpolation values, which is how every success toast asks for
    // its description. Read as options, the default is dropped and the key
    // itself reaches the screen.
    expect(provider.translate("notifications.success", "Success")).toBe("Success");
    expect(provider.translate("greeting", { namespace: "ui", name: "Ada" })).toBe(
      "Hello Ada",
    );
  });

  test("keeps a colon in a key instead of reading it as a namespace", () => {
    // Resource identifiers are `<schema>:<modelLabel>` and refine builds label
    // keys out of them, so ":" must not split off a namespace.
    const { provider } = createAngeeI18nRuntime({
      ui: { "console:projects.Project.console:projects.Project": "Project" },
    });

    expect(
      provider.translate(
        "console:projects.Project.console:projects.Project",
        "console:projects.Project",
      ),
    ).toBe("Project");
  });

  test("tracks Refine locale state", async () => {
    const { provider } = createAngeeI18nRuntime(resources);

    expect(provider.getLocale()).toBe("en");
    await provider.changeLocale("fr");
    expect(provider.getLocale()).toBe("fr");
  });
});

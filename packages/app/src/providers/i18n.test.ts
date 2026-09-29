import { describe, expect, test } from "vitest";

import { composeAppVocabulary, createAngeeI18nRuntime } from "./i18n";
import { MenuTree } from "@angee/ui/chrome/menu-tree";
import { testDataResource } from "@angee/metadata/testing";
import type { AppVocabulary } from "@angee/ui/runtime";

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

  test("tracks Refine locale state", async () => {
    const { provider } = createAngeeI18nRuntime(resources);

    expect(provider.getLocale()).toBe("en");
    await provider.changeLocale("fr");
    expect(provider.getLocale()).toBe("fr");
  });
});

describe("scoped vocabulary validation", () => {
  const models = [testDataResource("notes.Note", { fields: [{ name: "title", kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }] })];
  const menu = MenuTree.from([{ id: "desk", to: "/desk" }]);
  const routes = [{ name: "desk.all", path: "/desk" }, { name: "desk.record", path: "/desk/$id", parent: "desk.all" }];
  const compose = (vocabulary: readonly AppVocabulary[]) => composeAppVocabulary(resources, vocabulary, models, menu, routes);

  test.each([
    { app: "missing" },
    { app: "desk", route: "missing" },
    { app: "desk", messages: { notes: { missing: "Unknown" } } },
    { app: "desk", menus: { missing: "Unknown" } },
    { app: "desk", resources: { "missing.Model": { label: "Unknown" } } },
    { app: "desk", resources: { "notes.Note": { fields: { missing: "Unknown" } } } },
  ] satisfies AppVocabulary[])("rejects unknown vocabulary references: %j", (declaration) => {
    expect(() => compose([declaration])).toThrow(/unknown/i);
  });

  test("inherits app vocabulary into records and restores base copy outside the app", () => {
    const resolve = compose([
      { app: "desk", messages: { notes: { title: "Documents" } }, resources: { "notes.Note": { pluralLabel: "Documents" } } },
      { app: "desk", route: "desk.all", messages: { notes: { title: "Reviews" } }, resources: { "notes.Note": { fields: { title: "Subject" } } } },
    ]);
    expect(resolve("desk", "desk.record").i18n.provider.translate("title", { namespace: "notes" })).toBe("Reviews");
    expect(resolve("desk", "desk.record").vocabulary.resources["notes.Note"]).toEqual({ pluralLabel: "Documents", fields: { title: "Subject" } });
    expect(resolve().i18n.provider.translate("title", { namespace: "notes" })).toBe("Notes");
    expect(resolve().vocabulary.resources).toEqual({});
  });

  test("rejects competing declarations and unknown host translations", () => {
    expect(() => compose([{ app: "desk" }, { app: "desk" }])).toThrow(/declared twice/);
    expect(() => composeAppVocabulary(resources, [], models, menu, routes, { notes: { missing: "Unknown" } })).toThrow(/unknown i18n key/);
  });

  test("keeps one locale across cached and newly entered scopes without leaking copy", async () => {
    const resolve = compose([{ app: "desk", messages: { notes: { title: "Documents" } } }]);
    const base = resolve();
    const scoped = resolve("desk");
    await scoped.i18n.provider.changeLocale("fr");
    expect(base.i18n.provider.getLocale()).toBe("fr");
    expect(resolve("desk", "desk.record").i18n.provider.getLocale()).toBe("fr");
    expect(base.i18n.provider.translate("title", { namespace: "notes" })).toBe("Notes");
    expect(scoped.i18n.provider.translate("title", { namespace: "notes" })).toBe("Documents");
  });
});

// @vitest-environment happy-dom

import { useState, type ReactNode } from "react";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { Storage as TestStorage } from "happy-dom";
import * as v from "valibot";

import {
  AppRuntimeProvider,
  type RuntimeAuthState,
  type RuntimeUserPreferences,
  type RuntimeUserPreferencesPatch,
} from "../runtime/runtime";
import { setColorSchemePreference } from "../lib/color-scheme";
import { defineTheme } from "./runtime.mjs";
import type { ThemeContribution } from "./index";
import {
  APPEARANCE_CACHE_KEY,
  APPEARANCE_PREFERENCE_KEY,
  AppearanceProvider,
  useAppearance,
  type AppearancePreferences,
  type HostAppearanceDefaults,
} from "./appearance";

const hex = v.pipe(v.string(), v.regex(/^#[0-9a-f]{6}$/i));
const optionsSchema = v.strictObject({ brand: hex, rail: hex });
const paper: ThemeContribution = {
  definition: defineTheme({
    contractVersion: 1,
    id: "example.paper",
    legacyIds: ["example.old-paper"],
    labelKey: "paper.label",
    descriptionKey: "paper.description",
    revision: 1,
    tokens: {
      shared: {},
      light: { "--surface-canvas": "#fafafa" },
      dark: { "--surface-canvas": "#111111" },
    },
    options: {
      version: 1,
      defaults: { brand: "#112233", rail: "#223344" },
      parse: (value) => v.parse(optionsSchema, value),
      resolve: (value) => ({ shared: { "--brand": value.brand, "--surface-rail": value.rail } }),
    },
  }),
};
const ink: ThemeContribution = {
  definition: defineTheme({
    contractVersion: 1,
    id: "example.ink",
    labelKey: "ink.label",
    descriptionKey: "ink.description",
    revision: 1,
    tokens: {
      shared: { "--brand": "#556677" },
      light: { "--surface-canvas": "#eeeeee" },
      dark: { "--surface-canvas": "#222222" },
    },
  }),
};
const saved: AppearancePreferences = {
  version: 1,
  themeId: paper.definition.id,
  colorScheme: "light",
  options: { version: 1, value: { rail: "#223344", brand: "#445566" } },
};
const changedOptions = { version: 1, value: { brand: "#abcdef", rail: "#334455" } };
const host: HostAppearanceDefaults = { themeId: ink.definition.id, colorScheme: "dark", fingerprint: "test-host" };

function renderAppearance({
  preferences = { [APPEARANCE_PREFERENCE_KEY]: saved, unrelated: { keep: true } },
  available = true,
  status = "authenticated",
  defaults = host,
}: {
  preferences?: RuntimeUserPreferences;
  available?: boolean;
  status?: RuntimeAuthState["status"];
  defaults?: HostAppearanceDefaults;
} = {}) {
  let stored = preferences;
  let committed: (next: RuntimeUserPreferences) => void = () => undefined;
  const patchPreferences = vi.fn(async (patch: RuntimeUserPreferencesPatch) => {
    stored = patch(stored);
    committed(stored);
  });
  const wrapper = ({ children }: { children: ReactNode }) => {
    const [preferences, setPreferences] = useState(stored);
    committed = setPreferences;
    return <AppRuntimeProvider runtime={{
      themes: [paper, ink],
      auth: { status, user: status === "authenticated" ? { id: "actor-1", name: "Example" } : null, hasRole: () => false },
      userPreferences: { available, preferences, patchPreferences },
    }}><AppearanceProvider host={defaults}>{children}</AppearanceProvider></AppRuntimeProvider>;
  };
  return { ...renderHook(useAppearance, { wrapper }), patchPreferences, stored: () => stored };
}

beforeEach(() => {
  // Pin the DOM storage instead of falling through to Node's global accessor.
  Object.defineProperty(window, "localStorage", { configurable: true, value: new TestStorage() });
  document.documentElement.removeAttribute("style");
  delete document.documentElement.dataset.themeId;
  delete document.documentElement.dataset.colorScheme;
  delete document.documentElement.dataset.theme;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("an open draft re-applies theme, scheme and options without persisting", async () => {
  const { result, patchPreferences, stored } = renderAppearance();
  act(() => result.current.openDraft());
  expect(result.current.dirty).toBe(false);
  expect(result.current.currentPreferences).toEqual(saved);

  await act(async () => {
    await result.current.setTheme(ink.definition.id);
    await result.current.setColorScheme("dark");
  });
  expect(result.current.preferences).toEqual(saved);
  expect(result.current.currentPreferences).toMatchObject({ themeId: ink.definition.id, colorScheme: "dark" });
  expect(result.current.dirty).toBe(true);
  expect(document.documentElement.dataset).toMatchObject({ themeId: ink.definition.id, colorScheme: "dark", theme: "dark" });
  expect(document.documentElement.style.colorScheme).toBe("dark");
  expect(document.documentElement.style.getPropertyValue("--brand")).toBe("#556677");

  await act(async () => {
    await result.current.setTheme(paper.definition.id);
    await result.current.setOptions(changedOptions);
  });
  expect(result.current.currentPreferences.options).toEqual(changedOptions);
  expect(result.current.effectiveOptions).toEqual(changedOptions);
  expect(document.documentElement.dataset.themeId).toBe(paper.definition.id);
  expect(document.documentElement.style.getPropertyValue("--brand")).toBe("#abcdef");
  expect(document.documentElement.style.getPropertyValue("--surface-canvas")).toBe("#111111");
  expect(document.documentElement.style.getPropertyValue("--surface-rail")).toBe("#334455");
  expect(result.current.saving).toBe(false);
  expect(patchPreferences).not.toHaveBeenCalled();
  expect(stored()[APPEARANCE_PREFERENCE_KEY]).toEqual(saved);
});

test("dirty compares values, including option key order and edits back to saved values", async () => {
  const { result, patchPreferences } = renderAppearance();
  act(() => result.current.openDraft());
  await act(async () => result.current.setOptions({ version: 1, value: { brand: "#445566", rail: "#223344" } }));
  expect(result.current.dirty).toBe(false);
  await act(async () => result.current.setOptions(changedOptions));
  expect(result.current.dirty).toBe(true);
  await act(async () => result.current.setTheme(saved.themeId, saved.options));
  expect(result.current.dirty).toBe(false);
  await act(async () => result.current.setColorScheme("dark"));
  expect(result.current.dirty).toBe(true);
  await act(async () => result.current.setColorScheme("light"));
  expect(result.current.dirty).toBe(false);
  expect(patchPreferences).not.toHaveBeenCalled();
});

test("opening an existing draft preserves edits", async () => {
  const { result } = renderAppearance();
  await act(async () => {
    result.current.openDraft();
    await result.current.setColorScheme("dark");
    result.current.openDraft();
  });
  expect(result.current.currentPreferences.colorScheme).toBe("dark");
  expect(result.current.dirty).toBe(true);
});

test("save persists once, preserves other preference slices and closes the draft", async () => {
  const { result, patchPreferences, stored } = renderAppearance();
  await act(async () => {
    result.current.openDraft();
    await result.current.setColorScheme("dark");
    await result.current.setOptions(changedOptions);
  });
  const draft = result.current.currentPreferences;
  await act(async () => result.current.save());
  expect(patchPreferences).toHaveBeenCalledOnce();
  expect(stored()).toEqual({ appearance: draft, unrelated: { keep: true } });
  expect(result.current.preferences).toEqual(draft);
  expect(result.current.currentPreferences).toEqual(draft);
  expect(result.current.dirty).toBe(false);
  expect(result.current.saving).toBe(false);
  expect(JSON.parse(window.localStorage.getItem(APPEARANCE_CACHE_KEY)!)).toMatchObject({
    themeId: paper.definition.id, colorScheme: "dark", options: changedOptions,
  });
  await act(async () => result.current.save());
  expect(patchPreferences).toHaveBeenCalledOnce();
  await act(async () => result.current.setColorScheme("light"));
  expect(patchPreferences).toHaveBeenCalledTimes(2);
  expect(stored().appearance).toMatchObject({ colorScheme: "light" });
});

test("discard closes the draft and restores the saved document appearance", async () => {
  const { result, patchPreferences } = renderAppearance();
  await act(async () => {
    result.current.openDraft();
    await result.current.setTheme(ink.definition.id);
    await result.current.setColorScheme("dark");
  });
  act(() => result.current.discard());
  expect(result.current.currentPreferences).toEqual(saved);
  expect(result.current.dirty).toBe(false);
  expect(document.documentElement.dataset).toMatchObject({ themeId: paper.definition.id, colorScheme: "light" });
  expect(document.documentElement.style.getPropertyValue("--brand")).toBe("#445566");
  expect(document.documentElement.style.getPropertyValue("--surface-canvas")).toBe("#fafafa");
  expect(patchPreferences).not.toHaveBeenCalled();
  await act(async () => result.current.setColorScheme("dark"));
  expect(patchPreferences).toHaveBeenCalledOnce();
});

test("the colour scheme setter persists immediately when no draft is open", async () => {
  const { result, patchPreferences, stored } = renderAppearance();
  await act(async () => result.current.setColorScheme("dark"));
  expect(patchPreferences).toHaveBeenCalledOnce();
  expect(stored().appearance).toEqual({ ...saved, colorScheme: "dark" });
  expect(result.current.preferences.colorScheme).toBe("dark");
  expect(result.current.currentPreferences).toEqual(result.current.preferences);
  expect(result.current.dirty).toBe(false);
  expect(document.documentElement.dataset.colorScheme).toBe("dark");
});

test("theme and option setters still persist immediately outside a draft", async () => {
  const { result, patchPreferences, stored } = renderAppearance();
  await act(async () => result.current.setOptions(changedOptions));
  expect(patchPreferences).toHaveBeenCalledOnce();
  expect(stored().appearance).toEqual({ ...saved, options: changedOptions });
  await act(async () => result.current.setTheme(ink.definition.id));
  expect(patchPreferences).toHaveBeenCalledTimes(2);
  expect(stored().appearance).toEqual({ version: 1, themeId: ink.definition.id, colorScheme: "light", options: undefined });
  expect(document.documentElement.dataset.themeId).toBe(ink.definition.id);
});

test("reset directly deletes saved preferences, closes the draft and applies the host", async () => {
  const { result, patchPreferences, stored } = renderAppearance();
  await act(async () => {
    result.current.openDraft();
    await result.current.setOptions(changedOptions);
    await result.current.reset();
  });
  expect(patchPreferences).toHaveBeenCalledOnce();
  expect(stored()).toEqual({ unrelated: { keep: true } });
  expect(result.current.preferences).toEqual({ version: 1 });
  expect(result.current.currentPreferences).toEqual({ version: 1 });
  expect(result.current.dirty).toBe(false);
  expect(document.documentElement.dataset).toMatchObject({ themeId: ink.definition.id, colorScheme: "dark" });
  expect(document.documentElement.style.getPropertyValue("--surface-rail")).toBe("");
  await act(async () => result.current.setColorScheme("light"));
  expect(patchPreferences).toHaveBeenCalledTimes(2);
});

test("draft-only edits never write the boot cache and remounting applies saved appearance", async () => {
  const { result, patchPreferences, unmount, stored } = renderAppearance();
  const cached = window.localStorage.getItem(APPEARANCE_CACHE_KEY);
  expect(cached).not.toBeNull();
  const writes = vi.spyOn(window.localStorage, "setItem");
  await act(async () => {
    result.current.openDraft();
    await result.current.setTheme(ink.definition.id);
    await result.current.setColorScheme("dark");
  });
  await act(async () => {
    await result.current.setTheme(paper.definition.id);
    await result.current.setOptions(changedOptions);
  });
  expect(window.localStorage.getItem(APPEARANCE_CACHE_KEY)).toBe(cached);
  expect(writes).not.toHaveBeenCalled();
  expect(patchPreferences).not.toHaveBeenCalled();
  unmount();
  const reloaded = renderAppearance({ preferences: stored() });
  expect(reloaded.result.current.currentPreferences).toEqual(saved);
  expect(reloaded.result.current.dirty).toBe(false);
  expect(document.documentElement.dataset).toMatchObject({ themeId: paper.definition.id, colorScheme: "light" });
  expect(document.documentElement.style.getPropertyValue("--brand")).toBe("#445566");
});

test("a failed save retains the draft, saved appearance cache and error, and permits retry", async () => {
  const { result, patchPreferences, stored } = renderAppearance();
  const cached = window.localStorage.getItem(APPEARANCE_CACHE_KEY);
  const failure = new Error("Preference write failed");
  await act(async () => {
    result.current.openDraft();
    await result.current.setColorScheme("dark");
  });
  patchPreferences.mockRejectedValueOnce(failure);
  await act(async () => { await expect(result.current.save()).rejects.toBe(failure); });
  expect(patchPreferences).toHaveBeenCalledOnce();
  expect(stored().appearance).toEqual(saved);
  expect(result.current.preferences).toEqual(saved);
  expect(result.current.currentPreferences.colorScheme).toBe("dark");
  expect(result.current.dirty).toBe(true);
  expect(result.current.error).toBe(failure);
  expect(result.current.saving).toBe(false);
  expect(document.documentElement.dataset.colorScheme).toBe("dark");
  expect(window.localStorage.getItem(APPEARANCE_CACHE_KEY)).toBe(cached);
  await act(async () => result.current.save());
  expect(stored().appearance).toEqual({ ...saved, colorScheme: "dark" });
  expect(result.current.dirty).toBe(false);
  expect(result.current.error).toBeNull();
});

test.each([
  { name: "unavailable preferences", available: false },
  { name: "resolving identity", status: "resolving" as const },
  { name: "unsupported saved version", preferences: { appearance: { version: 2 } } },
])("does not open a draft with $name", async ({ name: _name, ...options }) => {
  const { result, patchPreferences } = renderAppearance(options);
  await act(async () => {
    result.current.openDraft();
    await result.current.save();
  });
  expect(result.current.currentPreferences).toEqual(result.current.preferences);
  expect(result.current.dirty).toBe(false);
  expect(patchPreferences).not.toHaveBeenCalled();
});

test("removing optional overrides becomes clean again and keeps following host defaults", async () => {
  const { result, patchPreferences } = renderAppearance({ preferences: {} });
  await act(async () => {
    result.current.openDraft();
    await result.current.setTheme(paper.definition.id);
    await result.current.setColorScheme("light");
  });
  expect(result.current.dirty).toBe(true);
  await act(async () => {
    await result.current.setTheme(undefined);
    await result.current.setColorScheme(undefined);
  });
  expect(result.current.dirty).toBe(false);
  expect(result.current.currentPreferences).toEqual({ version: 1 });
  expect(document.documentElement.dataset).toMatchObject({ themeId: ink.definition.id, colorScheme: "dark" });
  expect(patchPreferences).not.toHaveBeenCalled();
});

test("legacy theme aliases remain clean and save through the canonical preference path", async () => {
  const { result, stored } = renderAppearance({ preferences: { appearance: { ...saved, themeId: "example.old-paper" } } });
  act(() => result.current.openDraft());
  expect(result.current.dirty).toBe(false);
  expect(result.current.preferences.themeId).toBe(paper.definition.id);
  await act(async () => result.current.save());
  expect(stored().appearance).toEqual(saved);
});

test("scheme commands use the same setter and stay in an open draft", async () => {
  const { result, patchPreferences } = renderAppearance();
  act(() => result.current.openDraft());
  await act(async () => setColorSchemePreference("dark"));
  expect(result.current.currentPreferences.colorScheme).toBe("dark");
  expect(result.current.preferences.colorScheme).toBe("light");
  expect(document.documentElement.dataset.colorScheme).toBe("dark");
  expect(patchPreferences).not.toHaveBeenCalled();
});

test("immediate scheme changes preserve malformed saved options until an explicit theme edit", async () => {
  const preferences = { appearance: { ...saved, options: { future: true } } };
  const { result, stored } = renderAppearance({ preferences });
  await act(async () => result.current.setColorScheme("dark"));
  expect(stored().appearance).toEqual({ ...preferences.appearance, colorScheme: "dark" });
  expect(result.current.notice).toBe("options-invalid");
});

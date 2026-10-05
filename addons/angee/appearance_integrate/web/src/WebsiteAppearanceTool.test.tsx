// @vitest-environment happy-dom

import { useState } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { installTestLocalStorage, ShellPageTestProviders } from "@angee/app/testing";
import { AppearanceProvider, useAppearance, type AppearanceState, type RuntimeUserPreferences, type RuntimeUserPreferencesPatch } from "@angee/ui";
import { createThemeCustomizationOptions, defineTheme, defineThemeContribution, parseThemeCustomization } from "@angee/ui/theme";

vi.mock("./documents", () => ({ AnalyseAppearance: {} }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: (_document: unknown, _variables: unknown, { enabled }: { enabled: boolean }) => ({
    data: enabled ? { analyseAppearance: {
      finalUrl: "https://example.test", title: "Example website", siteName: "", colors: ["#abcdef", "#123456"], neutralTint: "#667788", fonts: [], warnings: [],
    } } : undefined,
    error: null, isFetching: false, refetch: vi.fn(),
  }),
}));

import { WebsiteAppearanceTool } from "./WebsiteAppearanceTool";

const paper = defineThemeContribution({
  definition: defineTheme({
    contractVersion: 1, id: "example.paper", labelKey: "paper.label", descriptionKey: "paper.description", revision: 1,
    tokens: { shared: {}, light: {}, dark: {} },
    options: createThemeCustomizationOptions({
      brand: "#112233", accent: "#334455", neutral: "#778899", canvas: "#fafafa", surface: "#ffffff", rail: "#112233",
      success: "#16845b", warning: "#b7791f", danger: "#c2413a", info: "#3178c6",
      font: "theme", radius: "theme", density: "theme", elevation: "theme", logo: "theme",
    }),
  }),
});
const fixed = defineThemeContribution({
  definition: defineTheme({
    contractVersion: 1, id: "example.fixed", labelKey: "fixed.label", descriptionKey: "fixed.description", revision: 1,
    tokens: { shared: {}, light: {}, dark: {} },
  }),
});

beforeEach(() => { installTestLocalStorage(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test.each([paper, fixed])("applying a website palette from $definition.id edits the open draft until Save", async (selected) => {
  const saved = { version: 1, themeId: selected.definition.id, colorScheme: "dark" };
  let stored: RuntimeUserPreferences = { appearance: saved, unrelated: true };
  let current!: AppearanceState;
  let commit!: (preferences: RuntimeUserPreferences) => void;
  const patchPreferences = vi.fn(async (patch: RuntimeUserPreferencesPatch) => {
    stored = patch(stored);
    commit(stored);
  });
  function Probe() { current = useAppearance(); return null; }
  function Fixture() {
    const [preferences, setPreferences] = useState(stored);
    commit = setPreferences;
    return <ShellPageTestProviders runtime={{
      themes: [paper, fixed],
      auth: { status: "authenticated", user: { id: "actor-1", name: "Example" }, hasRole: () => false },
      userPreferences: { available: true, preferences, patchPreferences },
    }}><AppearanceProvider><Probe /><WebsiteAppearanceTool /></AppearanceProvider></ShellPageTestProviders>;
  }
  render(<Fixture />);
  act(() => current.openDraft());
  fireEvent.change(screen.getByLabelText("Website URL"), { target: { value: "https://example.test" } });
  fireEvent.click(screen.getByRole("button", { name: "Analyse" }));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Customize paper.label" })); });
  expect(current.currentPreferences).toMatchObject({ themeId: paper.definition.id, colorScheme: "dark" });
  expect(parseThemeCustomization(current.currentPreferences.options?.value)).toMatchObject({ brand: "#abcdef", accent: "#123456", neutral: "#667788" });
  expect(current.preferences).toEqual(saved);
  expect(current.dirty).toBe(true);
  expect(current.saving).toBe(false);
  expect(patchPreferences).not.toHaveBeenCalled();
  expect(stored.appearance).toEqual(saved);
  await act(async () => { await current.save(); });
  expect(patchPreferences).toHaveBeenCalledOnce();
  expect(current.dirty).toBe(false);
  expect(stored).toEqual({ appearance: current.currentPreferences, unrelated: true });
});

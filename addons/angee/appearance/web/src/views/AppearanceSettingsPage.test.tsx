// @vitest-environment happy-dom

import { StrictMode, useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryHistory, createRootRoute, createRoute, createRouter, Outlet, RouterProvider } from "@tanstack/react-router";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { composeAddons } from "@angee/app/define-addon";
import { installTestLocalStorage, ShellPageTestProviders } from "@angee/app/testing";
import { AppearanceProvider, ControlBandProvider, dirtyControlBandClassName, ModalsHost, useAppearance, type AppearanceState, type RuntimeUserPreferences, type RuntimeUserPreferencesPatch } from "@angee/ui";
import { createThemeCustomizationOptions, defineTheme, defineThemeContribution, parseThemeCustomization, resolveThemeOptions, ThemeCustomizationEditor, type AppearancePreferences } from "@angee/ui/theme";

import appearanceAddon from "../index";
import { AppearanceSettingsPage } from "./AppearanceSettingsPage";

function theme(name: string, brand: string) {
  return defineThemeContribution({
    definition: defineTheme({
      contractVersion: 1, id: `example.${name}`, labelKey: `${name}.label`, descriptionKey: `${name}.description`, revision: 1,
      tokens: { shared: {}, light: { "--surface-canvas": "#fafafa" }, dark: { "--surface-canvas": "#111111" } },
      options: createThemeCustomizationOptions({
        brand, accent: "#447788", neutral: "#667788", canvas: "#fafafa", surface: "#ffffff", rail: "#112233",
        success: "#16845b", warning: "#b7791f", danger: "#c2413a", info: "#3178c6",
        font: "theme", radius: "theme", density: "theme", elevation: "theme", logo: "theme",
      }),
    }),
    optionsEditor: ThemeCustomizationEditor,
  });
}
const paper = theme("paper", "#112233");
const ink = theme("ink", "#334455");
const saved: AppearancePreferences = {
  version: 1, themeId: paper.definition.id, colorScheme: "light",
  options: { version: paper.definition.options!.version, value: { ...parseThemeCustomization(paper.definition.options!.defaults), brand: "#445566" } },
};
const { containers } = composeAddons([appearanceAddon], { canonicalModelLabel: (label) => label });

async function fixture(initial: RuntimeUserPreferences = { appearance: saved, unrelated: true }) {
  let stored = initial;
  let current!: AppearanceState;
  let commit!: (preferences: RuntimeUserPreferences) => void;
  let setMounted!: (mounted: boolean) => void;
  const patchPreferences = vi.fn(async (patch: RuntimeUserPreferencesPatch) => {
    stored = patch(stored);
    commit(stored);
  });
  function Probe() { current = useAppearance(); return null; }
  function Root() {
    const [preferences, setPreferences] = useState(stored);
    const [host, setHost] = useState<HTMLDivElement | null>(null);
    const [mounted, changeMounted] = useState(true);
    commit = setPreferences;
    setMounted = changeMounted;
    return <ShellPageTestProviders runtime={{
      themes: [paper, ink], containers,
      auth: { status: "authenticated", user: { id: "actor-1", name: "Example" }, hasRole: () => false },
      userPreferences: { available: true, preferences, patchPreferences },
    }}><AppearanceProvider host={{ themeId: paper.definition.id, colorScheme: "light" }}><ModalsHost>
      <Probe />
      <div ref={setHost} data-testid="control-host" />
      <ControlBandProvider host={host}>{mounted ? <Outlet /> : null}</ControlBandProvider>
    </ModalsHost></AppearanceProvider></ShellPageTestProviders>;
  }
  const root = createRootRoute({ component: Root });
  const settings = createRoute({ getParentRoute: () => root, path: "settings/appearance", component: AppearanceSettingsPage });
  const away = createRoute({ getParentRoute: () => root, path: "away", component: () => <p>Other page</p> });
  const router = createRouter({ routeTree: root.addChildren([settings, away]), history: createMemoryHistory({ initialEntries: ["/settings/appearance"] }) });
  render(<StrictMode><RouterProvider router={router} /></StrictMode>);
  await screen.findByRole("heading", { name: "Appearance", level: 1 });
  return { router, patchPreferences, stored: () => stored, appearance: () => current, hide: () => setMounted(false) };
}

const radio = (name: string | RegExp) => screen.getByRole("radio", { name });
const brandInput = () => screen.getByLabelText<HTMLInputElement>("Brand color");
function expectClean() {
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Discard" })).toBeNull();
  expect(screen.getByTestId("control-host").querySelector(`.${dirtyControlBandClassName}`)).toBeNull();
}

beforeEach(() => {
  installTestLocalStorage();
  document.documentElement.removeAttribute("style");
  delete document.documentElement.dataset.themeId;
  delete document.documentElement.dataset.colorScheme;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("theme, scheme and options edit the live provider draft and both previews without saving", async () => {
  const f = await fixture();
  expectClean();
  fireEvent.click(radio(/ink.label/));
  fireEvent.click(radio("Dark"));
  fireEvent.change(brandInput(), { target: { value: "#abcdef" } });
  await waitFor(() => expect(f.appearance().currentPreferences).toMatchObject({ themeId: ink.definition.id, colorScheme: "dark", options: { value: { brand: "#abcdef" } } }));
  expect(f.appearance().preferences).toEqual(saved);
  expect(f.appearance().dirty).toBe(true);
  expect(f.patchPreferences).not.toHaveBeenCalled();
  expect(f.stored()).toEqual({ appearance: saved, unrelated: true });
  expect(radio(/ink.label/).getAttribute("aria-checked")).toBe("true");
  expect(radio("Dark").getAttribute("aria-checked")).toBe("true");
  expect(document.documentElement.dataset).toMatchObject({ themeId: ink.definition.id, colorScheme: "dark" });
  const resolved = resolveThemeOptions(ink.definition, f.appearance().currentPreferences.options);
  expect(document.documentElement.style.getPropertyValue("--brand")).toBe(resolved.tokens.dark["--brand"]);
  for (const scheme of ["light", "dark"] as const) {
    const frame = screen.getByTitle<HTMLIFrameElement>(`${ink.definition.id} ${scheme} preview`);
    fireEvent.load(frame);
    await waitFor(() => expect(frame.contentDocument?.documentElement.style.getPropertyValue("--brand")).toBe(resolved.tokens[scheme]["--brand"]));
    expect(frame.contentDocument?.documentElement.dataset.colorScheme).toBe(scheme);
  }
  const band = screen.getByTestId("control-host");
  expect(within(band).getByRole("button", { name: "Save" })).toBeTruthy();
  expect(within(band).getByRole("button", { name: "Discard" })).toBeTruthy();
  expect(band.firstElementChild?.classList.contains(dirtyControlBandClassName)).toBe(true);
  expect(screen.queryByRole("button", { name: "Apply customization" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Cancel changes" })).toBeNull();
});

test("Save persists once, clears dirty and keeps the page ready to draft the next edit", async () => {
  const f = await fixture();
  fireEvent.click(radio(/ink.label/));
  fireEvent.click(radio("Dark"));
  fireEvent.change(brandInput(), { target: { value: "#abcdef" } });
  const draft = f.appearance().currentPreferences;
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.appearance().dirty).toBe(false));
  expect(f.patchPreferences).toHaveBeenCalledOnce();
  expect(f.stored()).toEqual({ appearance: draft, unrelated: true });
  expectClean();
  fireEvent.click(radio("Light"));
  expect(f.appearance().dirty).toBe(true);
  expect(f.appearance().currentPreferences.colorScheme).toBe("light");
  expect(f.appearance().preferences).toEqual(draft);
  expect(f.patchPreferences).toHaveBeenCalledOnce();
});

test("Discard restores saved values in every control and permits another draft", async () => {
  const f = await fixture();
  fireEvent.click(radio(/ink.label/));
  fireEvent.click(radio("Dark"));
  fireEvent.change(brandInput(), { target: { value: "#abcdef" } });
  fireEvent.click(screen.getByRole("button", { name: "Discard" }));
  expect(f.appearance().currentPreferences).toEqual(saved);
  expect(radio(/paper.label/).getAttribute("aria-checked")).toBe("true");
  expect(radio("Light").getAttribute("aria-checked")).toBe("true");
  expect(brandInput().value).toBe("#445566");
  expect(document.documentElement.dataset).toMatchObject({ themeId: paper.definition.id, colorScheme: "light" });
  expectClean();
  fireEvent.change(brandInput(), { target: { value: "#abcdef" } });
  expect(f.appearance().dirty).toBe(true);
  expect(f.patchPreferences).not.toHaveBeenCalled();
});

test("Restore theme defaults edits the draft while Reset appearance deletes the saved preference directly", async () => {
  const f = await fixture();
  fireEvent.click(screen.getByRole("button", { name: "Restore theme defaults" }));
  expect(brandInput().value).toBe("#112233");
  expect(f.appearance().dirty).toBe(true);
  expect(f.patchPreferences).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Reset appearance" }));
  await waitFor(() => expect(f.appearance().saving).toBe(false));
  expect(f.patchPreferences).toHaveBeenCalledOnce();
  expect(f.stored()).toEqual({ unrelated: true });
  expect(f.appearance().currentPreferences).toEqual({ version: 1 });
  expectClean();
  expect(screen.getAllByRole("radio", { name: /Follow app default/ }).every((control) => control.getAttribute("aria-checked") === "true")).toBe(true);
  fireEvent.click(radio("Dark"));
  expect(f.appearance().dirty).toBe(true);
  expect(f.patchPreferences).toHaveBeenCalledOnce();
});

test("unmounting just the page discards and closes its draft under Strict Mode", async () => {
  const f = await fixture();
  fireEvent.click(radio("Dark"));
  act(f.hide);
  expect(f.appearance().currentPreferences).toEqual(saved);
  expect(f.appearance().dirty).toBe(false);
  expect(document.documentElement.dataset.colorScheme).toBe("light");
  expect(f.patchPreferences).not.toHaveBeenCalled();
  await act(async () => { await f.appearance().setColorScheme("dark"); });
  expect(f.patchPreferences).toHaveBeenCalledOnce();
  expect(f.stored().appearance).toMatchObject({ colorScheme: "dark" });
});

test("dirty navigation can stay with the draft or leave and restore the saved app", async () => {
  const f = await fixture();
  fireEvent.click(radio("Dark"));
  act(() => { void f.router.navigate({ to: "/away" }); });
  fireEvent.click(await screen.findByRole("button", { name: "Stay" }));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  expect(f.router.state.location.pathname).toBe("/settings/appearance");
  expect(f.appearance().dirty).toBe(true);
  act(() => { void f.router.navigate({ to: "/away" }); });
  fireEvent.click(await screen.findByRole("button", { name: "Leave" }));
  await screen.findByText("Other page");
  expect(f.appearance().currentPreferences).toEqual(saved);
  expect(f.appearance().dirty).toBe(false);
  expect(document.documentElement.dataset.colorScheme).toBe("light");
  expect(f.patchPreferences).not.toHaveBeenCalled();
});

test("read-only appearance has disabled controls, no draft or actions, and direct Reset recovery", async () => {
  const f = await fixture({ appearance: { version: 2 }, unrelated: true });
  expect(f.appearance().editable).toBe(false);
  expectClean();
  expect(screen.getAllByRole("radio").every((control) => control.hasAttribute("disabled") || control.getAttribute("aria-disabled") === "true")).toBe(true);
  expect(brandInput().disabled).toBe(true);
  expect(f.appearance().dirty).toBe(false);
  fireEvent.click(radio("Dark"));
  expect(f.patchPreferences).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Reset appearance" }));
  await waitFor(() => expect(f.appearance().editable).toBe(true));
  expect(f.stored()).toEqual({ unrelated: true });
  expectClean();
  fireEvent.click(radio("Dark"));
  expect(f.appearance().dirty).toBe(true);
  expect(f.patchPreferences).toHaveBeenCalledOnce();
});

test("pending Save disables the band actions and controls; a failed Save keeps the draft for retry", async () => {
  const f = await fixture();
  const persist = f.patchPreferences.getMockImplementation()!;
  let reject!: (error: Error) => void;
  f.patchPreferences.mockImplementationOnce(() => new Promise<void>((_resolve, fail) => { reject = fail; }));
  fireEvent.click(radio("Dark"));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Discard" }).disabled).toBe(true);
  expect(screen.getByRole<HTMLButtonElement>("button", { name: "Save" }).disabled).toBe(true);
  expect(brandInput().disabled).toBe(true);
  await act(async () => { reject(new Error("Preference write failed")); });
  expect(await screen.findByText("Preference write failed")).toBeTruthy();
  expect(f.appearance().dirty).toBe(true);
  expect(f.appearance().currentPreferences.colorScheme).toBe("dark");
  expect(f.stored().appearance).toEqual(saved);
  f.patchPreferences.mockImplementation(persist);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(f.appearance().dirty).toBe(false));
  expect(f.patchPreferences).toHaveBeenCalledTimes(2);
  expectClean();
});

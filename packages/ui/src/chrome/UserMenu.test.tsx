// @vitest-environment happy-dom

import { useCallback, useState, type ReactNode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { RouterContextProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { Storage as TestStorage } from "happy-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { setColorSchemePreference, type ColorScheme, type ColorSchemePreference } from "../lib/color-scheme";
import { AppRuntimeProvider, type RuntimeUserPreferences, type RuntimeUserPreferencesPatch } from "../runtime/runtime";
import { AppearanceProvider } from "../theme/appearance";
import { UserMenu } from "./UserMenu";

const SYSTEM_DARK = "(prefers-color-scheme: dark)";
const auth = { status: "authenticated" as const, user: { id: "actor-1", name: "Example" }, hasRole: () => false };
let systemDark = false;
let schemeQueries: Set<(event: MediaQueryListEvent) => void>[] = [];

beforeEach(() => {
  // Pin the DOM storage instead of falling through to Node's global accessor.
  Object.defineProperty(window, "localStorage", { configurable: true, value: new TestStorage() });
  document.documentElement.removeAttribute("style");
  delete document.documentElement.dataset.colorScheme;
  systemDark = false;
  schemeQueries = [];
  vi.spyOn(window, "matchMedia").mockImplementation((query) => {
    const listeners = new Set<(event: MediaQueryListEvent) => void>();
    if (query === SYSTEM_DARK) schemeQueries.push(listeners);
    return {
      media: query,
      get matches() { return query === SYSTEM_DARK && systemDark; },
      onchange: null,
      addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => { listeners.add(listener); },
      removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => { listeners.delete(listener); },
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => true,
    } as unknown as MediaQueryList;
  });
});
afterEach(() => {
  cleanup();
  setColorSchemePreference("system");
  vi.restoreAllMocks();
});

/** The OS scheme changes: as in a browser, each query's change event is its own dispatch, oldest query first. */
function changeSystemScheme(dark: boolean): void {
  systemDark = dark;
  for (const listeners of [...schemeQueries]) {
    for (const listener of [...listeners]) act(() => listener({ matches: dark, media: SYSTEM_DARK } as MediaQueryListEvent));
  }
}

function expectDocumentScheme(scheme: ColorScheme): void {
  expect(document.documentElement.dataset.colorScheme).toBe(scheme);
  expect(document.documentElement.style.colorScheme).toBe(scheme);
}

function renderInRouter(tree: ReactNode) {
  const router = createRouter({ routeTree: createRootRoute(), history: createMemoryHistory({ initialEntries: ["/"] }) });
  return render(<RouterContextProvider router={router}>{tree}</RouterContextProvider>);
}

function WithAppearance({ saved, children }: { saved: ColorSchemePreference; children: ReactNode }) {
  const [preferences, setPreferences] = useState<RuntimeUserPreferences>({ appearance: { version: 1, colorScheme: saved } });
  const patchPreferences = useCallback(async (patch: RuntimeUserPreferencesPatch) => setPreferences((current) => patch(current)), []);
  return (
    <AppRuntimeProvider runtime={{ auth, userPreferences: { available: true, preferences, patchPreferences } }}>
      <AppearanceProvider>{children}</AppearanceProvider>
    </AppRuntimeProvider>
  );
}

/** Mounts the menu on demand, so the provider has applied and does not render again. */
function MenuOnDemand() {
  const [shown, setShown] = useState(false);
  return shown ? <UserMenu /> : <button type="button" onClick={() => setShown(true)}>Show the user menu</button>;
}

test("a user menu mounted after the provider applied leaves the saved scheme on the document", () => {
  renderInRouter(<WithAppearance saved="dark"><MenuOnDemand /></WithAppearance>);
  expectDocumentScheme("dark");
  fireEvent.click(screen.getByRole("button", { name: "Show the user menu" }));
  expect(screen.getByRole("button", { name: "User menu" })).toBeTruthy();
  expectDocumentScheme("dark");
});

test("with a provider, an OS scheme change leaves an explicit choice alone and moves a system choice", async () => {
  systemDark = true;
  renderInRouter(<WithAppearance saved="dark"><MenuOnDemand /></WithAppearance>);
  fireEvent.click(screen.getByRole("button", { name: "Show the user menu" }));
  expectDocumentScheme("dark");
  changeSystemScheme(false);
  expectDocumentScheme("dark");
  await act(async () => setColorSchemePreference("system"));
  expectDocumentScheme("light");
  changeSystemScheme(true);
  expectDocumentScheme("dark");
  expect(window.localStorage.getItem("angee:color-scheme")).toBeNull();
});

test("with a provider, a storage event leaves the document's scheme alone", () => {
  renderInRouter(<WithAppearance saved="dark"><UserMenu /></WithAppearance>);
  expectDocumentScheme("dark");
  act(() => { window.dispatchEvent(new StorageEvent("storage", { key: null })); });
  expectDocumentScheme("dark");
});

test("without a provider, the menu keeps the browser-local scheme, following the OS under system", async () => {
  renderInRouter(<AppRuntimeProvider runtime={{ auth }}><UserMenu /></AppRuntimeProvider>);
  expectDocumentScheme("light");
  changeSystemScheme(true);
  expectDocumentScheme("dark");
  fireEvent.click(screen.getByRole("button", { name: "User menu" }));
  fireEvent.click(await screen.findByRole("menuitem", { name: "Switch to light mode" }));
  expect(window.localStorage.getItem("angee:color-scheme")).toBe("light");
  expectDocumentScheme("light");
});

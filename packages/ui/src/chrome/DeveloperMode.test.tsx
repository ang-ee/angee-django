// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";

import {
  AppRuntimeProvider,
  applyDeveloperModeSearch,
  useDeveloperModeSwitch,
  type AppRuntime,
  type RuntimeComposition,
  type RuntimeUserPreferences,
} from "../runtime";
import { AppMenu } from "./AppMenu";
import { AppRailTree } from "./AppRailTree";
import { DeveloperMenu, useDeveloperFieldTitle, useDeveloperRail } from "./DeveloperMode";
import { MenuTree } from "./menu-tree";

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
});

const composition: RuntimeComposition = {
  shell: { brand: null, perspective: { id: "desk", root: "desk" }, provenance: { perspective: "desk" }, diagnostics: [] },
  effective: { home: "/desk", confineTo: "desk" },
  menus: {
    provenance: { "desk.notes": { label: "desk", sequence: "suite" } },
    removed: [
      { id: "desk.board", route: "desk.board", by: "suite", parent: "desk", label: "Board" },
      { id: "desk.reports.old", by: "suite", parent: "desk.reports", label: "Old reports" },
      { id: "legacy", by: "suite", label: "Legacy" },
    ],
    hidden: [{ id: "desk.archive", by: "suite", reason: "hide" }],
    unavailable: { "desk.board": 'menu item "desk.board" was removed' },
    diagnostics: [],
  },
};

const tree = MenuTree.from([{ id: "desk", label: "Desk", to: "/desk", children: [
  { id: "desk.notes", label: "Notes", to: "/desk/notes" },
  { id: "desk.archive", label: "Archive", to: "/desk/archive", hidden: true },
  { id: "desk.reports", label: "Reports", to: "/desk/reports" },
] }]);

function renderChrome(): void {
  const root = createRootRoute({ component: () => <AppRuntimeProvider runtime={{ composition, activeRouteName: "desk.home", activeApp: "desk" }}>
    <AppRailTree scope="apps" roots={tree.roots} activeRootId="desk" defaultOpenRootId="desk" />
    <AppMenu menuItems={tree} />
    <DeveloperMenu />
    <Outlet />
  </AppRuntimeProvider> });
  const home = createRoute({ getParentRoute: () => root, path: "/desk" });
  const router = createRouter({ routeTree: root.addChildren([home]), history: createMemoryHistory({ initialEntries: ["/desk"] }) });
  render(<RouterProvider router={router} />);
}

function runtimeWrapper(runtime: Partial<AppRuntime>) {
  return ({ children }: { children: ReactNode }) => <AppRuntimeProvider runtime={runtime}>{children}</AppRuntimeProvider>;
}

describe("developer mode", () => {
  test("is off by default: hidden and removed menus stay out of the top bar and rail, and no debug button shows", async () => {
    renderChrome();
    expect(await screen.findByRole("link", { name: "Notes" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Archive (hidden)" })).toBeNull();
    expect(screen.queryByText(/removed by suite/)).toBeNull();
    expect(screen.getByRole("link", { name: "Reports" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Composition" })).toBeNull();
  });

  test("?debug=1 shows hidden and removed menus where they were, removed apps in the rail, and the composition", async () => {
    applyDeveloperModeSearch("1");
    renderChrome();
    // The top bar (G-20) carries the app's own menus, hidden ones marked and removed ones struck through.
    expect(await screen.findByRole("link", { name: "Archive (hidden)" })).toBeTruthy();
    expect(screen.getByText("Board — removed by suite")).toBeTruthy();
    // A menu whose children were all removed opens to show them.
    fireEvent.click(screen.getByRole("button", { name: "Reports" }));
    expect(await screen.findByText("Old reports — removed by suite")).toBeTruthy();
    // The rail lists apps; a removed app shows at its root.
    expect(screen.getByText("Legacy — removed by suite")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Composition" }));
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(screen.getAllByText("desk.home").length).toBeGreaterThan(0);
    expect(screen.getByText("desk.archive ← suite (hide)")).toBeTruthy();
    expect(screen.getByText('desk.board: menu item "desk.board" was removed')).toBeTruthy();
    expect(window.sessionStorage.getItem("angee:developer-mode")).toBe("1");
  });

  test("the rail describes each item's id, the layers that shaped it and why it is hidden", () => {
    applyDeveloperModeSearch("1");
    const { result } = renderHook(() => useDeveloperRail(), { wrapper: runtimeWrapper({ composition }) });
    expect(result.current.describe(tree.byId.get("desk.notes")!)).toBe("desk.notes · ← desk, suite");
    expect(result.current.describe(tree.byId.get("desk.archive")!)).toBe("desk.archive · hidden by suite (hide)");
    expect(result.current.children(tree.byId.get("desk")!).map((item) => item.id)).toEqual(["desk.notes", "desk.archive", "desk.reports"]);
    expect(MenuTree.from([{ id: "a", to: "/a" }, { id: "b", to: "/b", hidden: true }]).railMenuItems(true).map((item) => item.id))
      .toEqual(["a", "b"]);
  });

  test("the switch stores the preference and wins for the session over the URL flag and the stored value", () => {
    let stored: RuntimeUserPreferences = { developerMode: true };
    const patchPreferences = vi.fn(async (patch: (current: RuntimeUserPreferences) => RuntimeUserPreferences) => {
      stored = patch(stored);
    });
    const { result } = renderHook(() => useDeveloperModeSwitch(), {
      wrapper: runtimeWrapper({ userPreferences: { available: true, preferences: { developerMode: true }, patchPreferences } }),
    });
    expect(result.current.enabled).toBe(true);
    act(() => applyDeveloperModeSearch("0"));
    expect(result.current.enabled).toBe(false);
    act(() => applyDeveloperModeSearch("1"));
    expect(result.current.enabled).toBe(true);
    // The URL is read on navigation, not on render: switching off holds while `?debug=1` stays in the address.
    act(() => result.current.setEnabled(false));
    expect(result.current.enabled).toBe(false);
    expect(stored).toEqual({ developerMode: false });
    expect(window.sessionStorage.getItem("angee:developer-mode")).toBe("0");
  });

  test("without stored preferences the switch holds for the session; field titles follow it", () => {
    const { result } = renderHook(() => ({ mode: useDeveloperModeSwitch(), title: useDeveloperFieldTitle() }), { wrapper: runtimeWrapper({}) });
    expect(result.current.mode.enabled).toBe(false);
    expect(result.current.title("due_date", "projects.Task")).toBeUndefined();
    act(() => result.current.mode.setEnabled(true));
    expect(result.current.mode.enabled).toBe(true);
    expect(result.current.title("due_date", "projects.Task")).toBe("Field: due_date · projects.Task");
    expect(result.current.title("due_date")).toBe("Field: due_date");
    act(() => result.current.mode.setEnabled(false));
    expect(result.current.mode.enabled).toBe(false);
  });
});

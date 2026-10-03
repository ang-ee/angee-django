// @vitest-environment happy-dom

import { act, cleanup, render, renderHook, screen } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import type { ReactNode } from "react";
import { afterEach, describe, expect, test } from "vitest";

import { AppRuntimeProvider, useDeveloperMode, type RuntimeComposition } from "../runtime";
import { AppRailTree } from "./AppRailTree";
import { DeveloperPanel } from "./DeveloperMode";
import { MenuTree } from "./menu-tree";

afterEach(() => {
  cleanup();
  window.sessionStorage.clear();
  window.history.replaceState({}, "", "/");
});

const composition: RuntimeComposition = {
  shell: { brand: null, perspective: { id: "desk", root: "desk" }, provenance: { perspective: "desk" }, diagnostics: [] },
  effective: { home: "/desk", confineTo: "desk" },
  menus: {
    provenance: { "desk.notes": { label: "desk", sequence: "suite" } },
    removed: [{ id: "desk.board", route: "desk.board", by: "suite", parent: "desk", label: "Board" }],
    hidden: [{ id: "desk.archive", by: "suite", reason: "hide" }],
    unavailable: { "desk.board": 'menu item "desk.board" was removed' },
    diagnostics: [],
  },
};

function renderRail(): void {
  const tree = MenuTree.from([{ id: "desk", label: "Desk", to: "/desk", children: [
    { id: "desk.notes", label: "Notes", to: "/desk/notes" },
    { id: "desk.archive", label: "Archive", to: "/desk/archive", hidden: true },
  ] }]);
  const root = createRootRoute({ component: () => <AppRuntimeProvider runtime={{ composition, activeRoute: "desk.home", activeApp: "desk" }}>
    <AppRailTree scope="apps" roots={tree.roots} activeRootId="desk" defaultOpenRootId="desk" />
    <DeveloperPanel />
    <Outlet />
  </AppRuntimeProvider> });
  const home = createRoute({ getParentRoute: () => root, path: "/desk" });
  const router = createRouter({ routeTree: root.addChildren([home]), history: createMemoryHistory({ initialEntries: ["/desk"] }) });
  render(<RouterProvider router={router} />);
}

describe("developer mode", () => {
  test("is off by default: the rail hides hidden and removed items and no panel shows", async () => {
    renderRail();
    expect(await screen.findByRole("link", { name: "Notes" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Archive" })).toBeNull();
    expect(screen.queryByText(/removed by suite/)).toBeNull();
    expect(screen.queryByText("Route")).toBeNull();
  });

  test("?debug=1 turns it on for the session: hidden and removed items, ids and the page panel show", async () => {
    window.history.replaceState({}, "", "/desk?debug=1");
    renderRail();
    const archive = await screen.findByRole("link", { name: "Archive" });
    expect(archive.getAttribute("title")).toBe("desk.archive · hidden by suite (hide)");
    expect(screen.getByRole("link", { name: "Notes" }).getAttribute("title")).toBe("desk.notes · ← desk, suite");
    expect(screen.getByText("Board — removed by suite")).toBeTruthy();
    expect(screen.getByText("desk.home")).toBeTruthy();
    expect(window.sessionStorage.getItem("angee:developer-mode")).toBe("1");
  });

  test("without stored preferences, the toggle switches the session flag", () => {
    const wrapper = ({ children }: { children: ReactNode }) => <AppRuntimeProvider runtime={{}}>{children}</AppRuntimeProvider>;
    const { result } = renderHook(() => useDeveloperMode(), { wrapper });
    expect(result.current.enabled).toBe(false);
    act(() => result.current.setEnabled(true));
    expect(result.current.enabled).toBe(true);
    act(() => result.current.setEnabled(false));
    expect(result.current.enabled).toBe(false);
  });
});

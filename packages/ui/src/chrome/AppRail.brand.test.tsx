// @vitest-environment happy-dom

import { cleanup, render, screen, within } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRouter } from "@tanstack/react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import { AppRail } from "./AppRail";
import { AppRailTree } from "./AppRailTree";
import { MenuTree, type ChromeMenuItem } from "./menu-tree";
import { APP_RAIL_PREFERENCES_KEY } from "./app-rail-preferences";

vi.mock("../lib/use-media-query", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/use-media-query")>()),
  useMediaQuery: () => true,
}));
afterEach(cleanup);

const menus: readonly ChromeMenuItem[] = [{
  id: "notes", label: "Notes root", to: "/notes", appRoot: true,
  children: [
    { id: "all", label: "All notes", to: "/notes", icon: "book" },
    { id: "archive", label: "Archive", to: "/notes/archive", icon: "archive" },
  ],
}];

describe("branded single-root rail", () => {
  test.each([true, false])("renders an accessible AppBrand with expanded=%s", async (expanded) => {
    const patchPreferences = vi.fn();
    const router = createRouter({
      history: createMemoryHistory({ initialEntries: ["/"] }),
      routeTree: createRootRoute({ component: () => <AppRuntimeProvider runtime={{
        brand: { name: "Notebook", mark: "notebook-mark" },
        icons: { "notebook-mark": () => <svg data-testid="brand-mark" /> },
        userPreferences: { available: true, preferences: { [APP_RAIL_PREFERENCES_KEY]: { expanded } }, patchPreferences },
      }}><AppRail menuItems={menus} /></AppRuntimeProvider> }),
    });
    render(<RouterProvider router={router} />);
    const brand = await screen.findByRole("link", { name: "Notebook" });
    expect(brand.getAttribute("href")).toBe("/notes");
    expect(within(brand).getByTestId("brand-mark")).toBeTruthy();
    expect(within(brand).getByText("Notebook").classList.contains("sr-only")).toBe(!expanded);
    const nav = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(within(nav).getByRole("link", { name: "All notes" }).getAttribute("href")).toBe("/notes");
    expect(within(nav).getByRole("link", { name: "Archive" }).getAttribute("href")).toBe("/notes/archive");
    expect(within(nav).queryByRole("link", { name: "Notes root" })).toBeNull();
    expect(screen.queryByRole("button", { name: /choose|switch app|reorder/i })).toBeNull();
    expect(patchPreferences).not.toHaveBeenCalled();
  });

  test("flat AppRailTree keeps a childless root navigable", async () => {
    const tree = MenuTree.from([{ id: "notes", label: "Notes", to: "/notes" }]);
    const router = createRouter({
      history: createMemoryHistory({ initialEntries: ["/"] }),
      routeTree: createRootRoute({ component: () => <AppRailTree scope="apps" flat roots={tree.roots} activeRootId="notes" /> }),
    });
    render(<RouterProvider router={router} />);
    expect((await screen.findByRole("link", { name: "Notes" })).getAttribute("href")).toBe("/notes");
    expect(screen.queryByRole("button", { name: /expand|collapse/i })).toBeNull();
  });
});

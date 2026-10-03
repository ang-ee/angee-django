// @vitest-environment happy-dom

import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import { AppRail } from "./AppRail";
import { MenuTree, type ChromeMenuItem } from "./menu-tree";

const media = vi.hoisted(() => ({ large: false }));
afterEach(() => cleanup());

vi.mock("../lib/use-media-query", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/use-media-query")>()),
  useMediaQuery: () => media.large,
}));

const menuItems: readonly ChromeMenuItem[] = [
  {
    id: "projects",
    label: "Projects",
    icon: "projects",
    appRoot: true,
    to: "/projects",
    children: [{ id: "projects.all", label: "All projects", to: "/projects/all" }],
  },
  {
    id: "notes",
    label: "Notes",
    icon: "notes",
    appRoot: true,
    to: "/notes",
    children: [{ id: "notes.all", label: "All notes", to: "/notes/all" }],
  },
  { id: "help", label: "Help", icon: "help", appRoot: true, to: "/help" },
  {
    id: "platform",
    label: "Platform",
    icon: "settings",
    group: "platform",
    to: "/settings",
    children: [{ id: "platform.apps", label: "Apps", to: "/settings/apps" }],
  },
];

describe("AppRail intermediate navigation", () => {
  test("a branded single-app rail keeps its menus out of both expanded and icon modes", async () => {
    media.large = true;
    const items: readonly ChromeMenuItem[] = [{ id: "desk", label: "Desk", to: "/desk", children: [
      { id: "desk.notes", label: "Notes", to: "/desk/notes" },
    ] }];
    function RailHost() {
      const [preferences, setPreferences] = useState<Record<string, unknown>>({});
      return <AppRuntimeProvider runtime={{
        brand: { name: "Desk brand", mark: "app-rail" },
        userPreferences: { available: true, preferences,
          patchPreferences: async (patch) => { setPreferences(patch); },
        },
      }}><AppRail menuItems={items} /></AppRuntimeProvider>;
    }
    const root = createRootRoute({ component: RailHost });
    const router = createRouter({ routeTree: root.addChildren([createRoute({ getParentRoute: () => root, path: "/desk" })]),
      history: createMemoryHistory({ initialEntries: ["/desk"] }) });
    render(<RouterProvider router={router} />);
    const collapse = await screen.findByRole("button", { name: "Collapse app navigation" });
    expect(screen.queryByRole("link", { name: "Notes" })).toBeNull();
    fireEvent.click(collapse);
    await screen.findByRole("button", { name: "Expand app navigation" });
    expect(screen.queryByRole("link", { name: "Notes" })).toBeNull();
    expect(screen.getByRole("link", { name: "Desk brand" })).toBeTruthy();
  });
  test("uses the supplied confined tree and retains shortcuts inside its root", async () => {
    media.large = true;
    const confined = MenuTree.from(menuItems).confineTo("projects");
    const rootRoute = createRootRoute({ component: () => <Outlet /> });
    const router = createRouter({
      routeTree: rootRoute.addChildren([createRoute({
        getParentRoute: () => rootRoute,
        path: "/projects",
        component: () => <AppRuntimeProvider runtime={{
          confineTo: "projects",
          userPreferences: {
            available: true,
            preferences: { "chrome.routeShortcuts": [
              { id: "project-1", label: "Saved project", path: "/projects/all/1" },
              { id: "note-1", label: "Saved note", path: "/notes/all/1" },
            ] },
            patchPreferences: async () => undefined,
          },
        }}>
          <AppRail menuItems={confined.roots} presentation="drawer" />
        </AppRuntimeProvider>,
      })]),
      history: createMemoryHistory({ initialEntries: ["/projects"] }),
    });
    render(<RouterProvider router={router} />);
    expect(await screen.findByRole("link", { name: "Saved project" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Saved note" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Notes" })).toBeNull();
  });

  test("opens submenu navigation without changing desktop preferences", async () => {
    media.large = false;
    const openNavigation = vi.fn();
    const patchPreferences = vi.fn();
    const rootRoute = createRootRoute({ component: () => <Outlet /> });
    const paths = ["/projects", "/projects/all", "/notes", "/notes/all", "/help", "/settings", "/settings/apps"] as const;
    const router = createRouter({
      routeTree: rootRoute.addChildren(paths.map((path) => createRoute({
        getParentRoute: () => rootRoute,
        path,
        component: () => (
          <AppRuntimeProvider runtime={{
            userPreferences: { available: true, preferences: {}, patchPreferences },
          }}>
            <AppRail menuItems={menuItems} onOpenNavigation={openNavigation} />
          </AppRuntimeProvider>
        ),
      }))),
      history: createMemoryHistory({ initialEntries: ["/projects"] }),
    });
    render(<RouterProvider router={router} />);

    expect(fireEvent.click(await screen.findByRole("link", { name: "Projects" }))).toBe(false);
    expect(openNavigation).toHaveBeenLastCalledWith("/projects");
    expect(router.state.location.pathname).toBe("/projects");

    expect(fireEvent.click(screen.getByRole("link", { name: "Notes" }))).toBe(false);
    expect(openNavigation).toHaveBeenLastCalledWith("/notes");
    expect(router.state.location.pathname).toBe("/projects");

    expect(fireEvent.click(screen.getByRole("link", { name: "Settings" }))).toBe(false);
    expect(openNavigation).toHaveBeenLastCalledWith("/settings");

    const requests = openNavigation.mock.calls.length;
    fireEvent.click(screen.getByRole("link", { name: "Notes" }), { ctrlKey: true });
    expect(openNavigation).toHaveBeenCalledTimes(requests);

    fireEvent.click(screen.getByRole("link", { name: "Help" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/help"));
    expect(openNavigation).toHaveBeenCalledTimes(requests);
    expect(patchPreferences).not.toHaveBeenCalled();
  });
});

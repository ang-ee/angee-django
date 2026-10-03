// @vitest-environment happy-dom

import { useState } from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
import { AppMenu } from "./AppMenu";
import { MenuTree, type ChromeMenuItem } from "./menu-tree";
import { ChromePlaceProvider } from "./refine-menu";

const media = vi.hoisted(() => ({ large: false }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

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
    children: [
      { id: "projects.all", label: "All projects", to: "/projects/all" },
      { id: "desk", label: "Desk", app: true, to: "/desk", children: [
        { id: "desk.inbox", label: "Inbox", to: "/desk/inbox" },
      ] },
    ],
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
  test("the rail and top bar share one memoized full-tree match, including preset changes", async () => {
    media.large = true;
    const tree = MenuTree.from([{ id: "m", label: "Messaging", to: "/m", children: [
      { id: "m.unread", label: "Unread", to: "/m/messages?preset=unread" },
      { id: "m.all", label: "All", to: "/m/messages" },
    ] }]);
    const match = vi.spyOn(tree, "match");
    function Host() {
      const [count, setCount] = useState(0);
      return <ChromePlaceProvider menuItems={tree}>
        <AppRail /><AppMenu /><button onClick={() => setCount(count + 1)}>Render {count}</button>
      </ChromePlaceProvider>;
    }
    const root = createRootRoute({ component: Host });
    const router = createRouter({ routeTree: root.addChildren([createRoute({
      getParentRoute: () => root, path: "/m/messages", validateSearch: (search) => search,
    })]), history: createMemoryHistory({ initialEntries: ["/m/messages"] }) });
    render(<RouterProvider router={router} />);
    const nav = await screen.findByRole("navigation", { name: "Messaging menu" });
    expect(within(nav).getByRole("link", { name: "All" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("navigation", { name: "Primary navigation" }).querySelector('[aria-current="true"]'))
      .toBeTruthy();
    expect(match).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Render 0" }));
    await screen.findByRole("button", { name: "Render 1" });
    expect(match).toHaveBeenCalledTimes(1);
    fireEvent.click(within(nav).getByRole("link", { name: "Unread" }));
    await waitFor(() => expect(within(nav).getByRole("link", { name: "Unread" }).getAttribute("aria-current")).toBe("page"));
    expect(match).toHaveBeenCalledTimes(2);
    expect(match).toHaveBeenLastCalledWith("/m/messages", "?preset=unread", false, undefined);
  });

  test("a hidden declaring root cannot make the pruned rail select a later route reference", async () => {
    media.large = true;
    const tree = MenuTree.from([
      { id: "hidden", label: "Hidden owner", hidden: true, to: "/shared" },
      { id: "visible", label: "Visible contributor", to: "/shared" },
    ]);
    const root = createRootRoute({ component: () => <ChromePlaceProvider menuItems={tree}>
      <AppRail /><AppMenu />
    </ChromePlaceProvider> });
    const router = createRouter({ routeTree: root.addChildren([createRoute({ getParentRoute: () => root, path: "/shared" })]),
      history: createMemoryHistory({ initialEntries: ["/shared"] }) });
    render(<RouterProvider router={router} />);
    const visible = await screen.findByRole("link", { name: "Visible contributor" });
    expect(visible.getAttribute("aria-current")).toBeNull();
    expect(visible.getAttribute("data-current")).toBe("false");
    expect(screen.queryByRole("navigation", { name: "Visible contributor menu" })).toBeNull();
  });

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

  test("opens included-app navigation only, without changing desktop preferences", async () => {
    media.large = false;
    const openNavigation = vi.fn();
    const patchPreferences = vi.fn();
    const rootRoute = createRootRoute({ component: () => <Outlet /> });
    const paths = ["/projects", "/projects/all", "/desk", "/desk/inbox", "/notes", "/notes/all", "/help", "/settings", "/settings/apps"] as const;
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
    expect(screen.getByRole("link", { name: "Projects" }).getAttribute("aria-current")).toBe("page");

    fireEvent.click(screen.getByRole("link", { name: "Notes" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/notes"));
    expect(openNavigation).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: "Notes" }).getAttribute("aria-haspopup")).toBeNull();
    expect(screen.getByRole("link", { name: "Notes" }).getAttribute("aria-current")).toBe("page");

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

  test("a branded sub-app navigates directly at intermediate widths despite owning menu children", async () => {
    media.large = false;
    const openNavigation = vi.fn();
    const tree = MenuTree.from(menuItems).confineTo("projects");
    const root = createRootRoute({ component: () => <AppRuntimeProvider runtime={{ confineTo: "projects" }}>
      <AppRail menuItems={tree.roots} onOpenNavigation={openNavigation} />
    </AppRuntimeProvider> });
    const router = createRouter({ routeTree: root.addChildren(["/desk", "/desk/inbox"].map((path) =>
      createRoute({ getParentRoute: () => root, path }))),
      history: createMemoryHistory({ initialEntries: ["/desk/inbox"] }) });
    render(<RouterProvider router={router} />);
    const desk = await screen.findByRole("link", { name: "Desk" });
    expect(desk.getAttribute("aria-current")).toBe("true");
    expect(desk.getAttribute("aria-haspopup")).toBeNull();
    fireEvent.click(desk);
    await waitFor(() => expect(router.state.location.pathname).toBe("/desk"));
    expect(desk.getAttribute("aria-current")).toBe("page");
    expect(openNavigation).not.toHaveBeenCalled();
  });

  test("icon-mode roots announce an ancestor app as current and only the exact page as page", async () => {
    media.large = false;
    const root = createRootRoute({ component: () => <AppRail menuItems={menuItems} /> });
    const router = createRouter({ routeTree: root.addChildren(["/notes", "/notes/all"].map((path) =>
      createRoute({ getParentRoute: () => root, path }))),
      history: createMemoryHistory({ initialEntries: ["/notes/all"] }) });
    render(<RouterProvider router={router} />);
    const notes = await screen.findByRole("link", { name: "Notes" });
    expect(notes.getAttribute("aria-current")).toBe("true");
    fireEvent.click(notes);
    await waitFor(() => expect(notes.getAttribute("aria-current")).toBe("page"));
  });
});

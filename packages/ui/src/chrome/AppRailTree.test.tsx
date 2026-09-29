// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { describe, expect, test, vi } from "vitest";

import { AppRailTree } from "./AppRailTree";
import { MenuTree } from "./menu-tree";

describe("AppRailTree", () => {
  test("follows a menu's preset through Router's full-href navigation", async () => {
    const tree = MenuTree.from([{ id: "desk", to: "/desk", children: [
      { id: "desk.notes", label: "Open notes", to: "/desk/notes?preset=desk.open" },
    ] }]);
    const root = createRootRoute({ component: () => <>
      <AppRailTree scope="apps" roots={tree.roots} activeRootId="desk" />
      <Outlet />
    </> });
    const home = createRoute({ getParentRoute: () => root, path: "/desk" });
    const notes = createRoute({ getParentRoute: () => root, path: "/desk/notes", validateSearch: (search) => search });
    const router = createRouter({ routeTree: root.addChildren([home, notes]),
      history: createMemoryHistory({ initialEntries: ["/desk"] }) });
    const view = within(render(<RouterProvider router={router} />).container);
    fireEvent.click(await view.findByRole("link", { name: "Open notes" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/desk/notes"));
    expect(router.state.location.search).toEqual({ preset: "desk.open" });
    expect(view.getByRole("link", { name: "Open notes" }).getAttribute("data-active")).toBe("true");
  });
  test("controls its accordion panel and includes badge metadata in its name", async () => {
    const tree = MenuTree.from([
      {
        id: "projects",
        label: "Projects",
        to: "/projects",
        badge: 3,
        children: [
          { id: "projects.all", label: "All projects", to: "/projects" },
        ],
      },
    ]);
    const rootRoute = createRootRoute({ component: () => <Outlet /> });
    const projectsRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/projects",
      component: () => (
        <AppRailTree
          scope="apps"
          roots={tree.railMenuItems()}
          activeRootId="projects"
        />
      ),
    });
    const router = createRouter({
      routeTree: rootRoute.addChildren([projectsRoute]),
      history: createMemoryHistory({ initialEntries: ["/projects"] }),
    });
    render(<RouterProvider router={router} />);

    const trigger = await screen.findByRole("button", {
      name: "Collapse Projects, 3 items",
    });
    const panelId = trigger.getAttribute("aria-controls");
    expect(panelId).toBeTruthy();
    expect(document.getElementById(panelId!)).toBeTruthy();

    fireEvent.click(trigger);
    expect(screen.getByRole("button", {
      name: "Expand Projects, 3 items",
    }).getAttribute("aria-expanded")).toBe("false");
  });

  test("only the most specific link is active and toggles the rail", async () => {
    const tree = MenuTree.from([
      {
        id: "projects",
        label: "Projects",
        to: "/projects",
        children: [
          { id: "projects.all", label: "All projects", to: "/projects" },
        ],
      },
      { id: "notes", label: "Notes", to: "/notes" },
    ]);
    const onActiveRootToggle = vi.fn();
    const rootRoute = createRootRoute({ component: () => <Outlet /> });
    const projectsRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/projects",
      component: () => (
        <AppRailTree
          scope="apps"
          roots={tree.railMenuItems()}
          activeRootId="projects"
          onActiveToggle={onActiveRootToggle}
        />
      ),
    });
    const router = createRouter({
      routeTree: rootRoute.addChildren([projectsRoute]),
      history: createMemoryHistory({ initialEntries: ["/projects"] }),
    });
    const view = within(render(<RouterProvider router={router} />).container);

    const parentLink = (await view.findByText("Projects")).closest("a")!;
    expect(parentLink.getAttribute("data-active")).toBe("false");
    expect(parentLink.getAttribute("aria-current")).toBeNull();
    const activeLink = (await view.findByText("All projects")).closest("a")!;
    expect(activeLink.getAttribute("data-active")).toBe("true");
    expect(activeLink.getAttribute("aria-current")).toBe("page");
    expect(fireEvent.click(activeLink)).toBe(false);
    expect(onActiveRootToggle).toHaveBeenCalledTimes(1);

    // An inactive root's link keeps its client-side navigation
    // (the router prevents default itself, so assert only the toggle).
    const inactiveLink = view.getByText("Notes").closest("a")!;
    fireEvent.click(inactiveLink);
    expect(onActiveRootToggle).toHaveBeenCalledTimes(1);
  });

  test("opens a requested inactive root without marking it active", async () => {
    const tree = MenuTree.from([
      {
        id: "projects",
        label: "Projects",
        to: "/projects",
        children: [{ id: "projects.all", label: "All projects", to: "/projects" }],
      },
      {
        id: "notes",
        label: "Notes",
        to: "/notes",
        children: [{ id: "notes.all", label: "All notes", to: "/notes" }],
      },
    ]);
    const rootRoute = createRootRoute({ component: () => <Outlet /> });
    const projectsRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: "/projects",
      component: () => (
        <AppRailTree
          scope="apps"
          roots={tree.railMenuItems()}
          activeRootId="projects"
          defaultOpenRootId="notes"
        />
      ),
    });
    const router = createRouter({
      routeTree: rootRoute.addChildren([projectsRoute]),
      history: createMemoryHistory({ initialEntries: ["/projects"] }),
    });
    render(<RouterProvider router={router} />);

    expect((await screen.findByText("Projects")).closest("a")?.getAttribute("data-active"))
      .toBe("false");
    expect(screen.getByText("Notes").closest("a")?.getAttribute("data-active"))
      .toBe("false");
    expect(screen.getByRole("button", { name: "Collapse Notes" }))
      .toBeTruthy();
    expect(screen.getByRole("button", { name: "Expand Projects" }))
      .toBeTruthy();
  });
});

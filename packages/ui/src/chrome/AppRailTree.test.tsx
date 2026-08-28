// @vitest-environment happy-dom

import { fireEvent, render, screen, within } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { beforeAll, describe, expect, test, vi } from "vitest";

import { AppRailTree } from "./AppRailTree";
import { MenuTree } from "./menu-tree";

describe("AppRailTree", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
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

  test("clicking the active root again fires the rail collapse toggle", async () => {
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

    // The active root's link toggles the rail instead of re-navigating…
    const activeLink = (await view.findByText("Projects")).closest("a")!;
    expect(activeLink.getAttribute("data-active")).toBe("true");
    expect(fireEvent.click(activeLink)).toBe(false);
    expect(onActiveRootToggle).toHaveBeenCalledTimes(1);

    // …while an inactive root's link keeps its client-side navigation
    // (the router prevents default itself, so assert only the toggle).
    const inactiveLink = view.getByText("Notes").closest("a")!;
    fireEvent.click(inactiveLink);
    expect(onActiveRootToggle).toHaveBeenCalledTimes(1);
  });
});

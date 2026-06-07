// @vitest-environment happy-dom

import { cleanup, render, screen, within } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import type { ReactElement } from "react";
import { afterEach, describe, expect, test } from "vitest";

import { Breadcrumb } from "./Breadcrumb";

afterEach(() => cleanup());

describe("Breadcrumb route matches", () => {
  test("renders the deepest static breadcrumb trail", async () => {
    renderBreadcrumbAt("/admin/users", [
      {
        path: "/admin",
        breadcrumbs: [{ label: "Admin" }],
      },
      {
        path: "/admin/users",
        breadcrumbs: [
          { label: "Admin", to: "/admin" },
          { label: "Users" },
        ],
      },
    ]);

    const breadcrumb = await screen.findByRole("navigation", {
      name: "Breadcrumb",
    });
    expect(within(breadcrumb).getByText("Admin").closest("a")?.getAttribute("href"))
      .toBe("/admin");
    expect(within(breadcrumb).getByText("Users").getAttribute("aria-current"))
      .toBe("page");
  });

  test("appends deeper crumb factories after the static trail", async () => {
    renderNestedBreadcrumbAt("/notes/first");

    const breadcrumb = await screen.findByRole("navigation", {
      name: "Breadcrumb",
    });
    expect(within(breadcrumb).getByText("Notes").closest("a")?.getAttribute("href"))
      .toBe("/notes");
    expect(within(breadcrumb).getByText("first").getAttribute("aria-current"))
      .toBe("page");
  });

  test("drops empty dynamic crumbs", async () => {
    renderNestedBreadcrumbAt("/notes/first", () => "");

    const breadcrumb = await screen.findByRole("navigation", {
      name: "Breadcrumb",
    });
    expect(within(breadcrumb).getByText("Notes").getAttribute("aria-current"))
      .toBe("page");
    expect(breadcrumb.textContent).toBe("Notes");
  });
});

function renderBreadcrumbAt(
  initialPath: string,
  routes: readonly StaticRoute[],
): void {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const routeNodes = routes.map((route) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path: route.path,
      staticData: {
        chrome: { breadcrumbs: route.breadcrumbs },
      },
      component: Breadcrumb,
    }),
  );
  const router = createRouter({
    routeTree: rootRoute.addChildren(routeNodes),
    history: createMemoryHistory({ initialEntries: [initialPath] }),
  });
  render(<RouterProvider router={router} />);
}

function renderNestedBreadcrumbAt(
  initialPath: string,
  crumb: (params: { id?: string }) => ReactElement | string | null = (params) =>
    params.id ?? null,
): void {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const notesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/notes",
    staticData: {
      chrome: { breadcrumbs: [{ label: "Notes" }] },
    },
    component: Breadcrumb,
  });
  const recordRoute = createRoute({
    getParentRoute: () => notesRoute,
    path: "$id",
    staticData: {
      breadcrumb: (match) => crumb(match.params as { id?: string }),
    },
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      notesRoute.addChildren([recordRoute]),
    ]),
    history: createMemoryHistory({ initialEntries: [initialPath] }),
  });
  render(<RouterProvider router={router} />);
}

interface StaticRoute {
  path: string;
  breadcrumbs: readonly { label: string; to?: string }[];
}

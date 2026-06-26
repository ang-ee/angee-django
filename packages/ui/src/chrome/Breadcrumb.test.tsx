// @vitest-environment happy-dom

import { cleanup, render, screen, within } from "@testing-library/react";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRouter,
} from "@tanstack/react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import { Breadcrumb } from "./Breadcrumb";

const refineMocks = vi.hoisted(() => ({
  breadcrumbs: [] as { label: string; href?: string }[],
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useBreadcrumb: () => ({ breadcrumbs: refineMocks.breadcrumbs }),
  };
});

afterEach(() => {
  cleanup();
  refineMocks.breadcrumbs = [];
});

describe("Breadcrumb", () => {
  test("renders the refine breadcrumb trail", async () => {
    refineMocks.breadcrumbs = [
      { label: "Notes", href: "/notes" },
      { label: "First note" },
    ];

    renderBreadcrumb();

    const breadcrumb = await screen.findByRole("navigation", {
      name: "Breadcrumb",
    });
    expect(within(breadcrumb).getByText("Notes").closest("a")?.getAttribute("href"))
      .toBe("/notes");
    expect(within(breadcrumb).getByText("First note").getAttribute("aria-current"))
      .toBe("page");
  });
});

function renderBreadcrumb(): void {
  const rootRoute = createRootRoute({ component: Breadcrumb });
  const router = createRouter({
    routeTree: rootRoute,
    history: createMemoryHistory({ initialEntries: ["/notes/first"] }),
  });
  render(<RouterProvider router={router} />);
}

// @vitest-environment happy-dom

import { render, screen, within } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { NuqsTestingAdapter } from "nuqs/adapters/testing";
import type { ReactNode, SVGProps } from "react";
import { beforeAll, describe, expect, test, vi } from "vitest";

import { ConsoleShell } from "./ConsoleShell";

vi.mock("@angee/logo-react", () => ({
  AngeeLogo: ({ bgColor: _bgColor, geometry: _geometry, preset: _preset, ...props }: SVGProps<SVGSVGElement> & {
    bgColor?: string | null;
    geometry?: string;
    preset?: string;
  }) => <svg {...props} data-testid="angee-logo" />,
}));

vi.mock("@angee/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/sdk")>();
  return {
    ...actual,
    useAuth: () => ({
      user: {
        id: "user_1",
        name: "Ada Lovelace",
        email: "ada@example.com",
      },
      status: "authenticated" as const,
      hasRole: () => false,
    }),
    useLogout: () => ({
      logout: vi.fn(async () => true),
      fetching: false,
      error: null,
    }),
    useMenus: () => [
      { id: "notes", label: "Notes", to: "/notes", icon: "file" },
      { id: "archive", label: "Archive", to: "/archive", icon: "archive" },
    ],
  };
});

function renderInRouter(children: ReactNode) {
  const rootRoute = createRootRoute({
    component: () => (
      <NuqsTestingAdapter>
        <Outlet />
      </NuqsTestingAdapter>
    ),
  });
  const notesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/notes",
    component: () => <>{children}</>,
  });
  const archiveRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/archive",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([notesRoute, archiveRoute]),
    history: createMemoryHistory({ initialEntries: ["/notes"] }),
  });

  return render(<RouterProvider router={router} />);
}

describe("ConsoleShell", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
  });

  test("composes rail navigation, top chrome, breadcrumbs, content, and chatter", async () => {
    renderInRouter(
      <ConsoleShell title="Notes" icon="file">
        <section aria-label="Page body">Body content</section>
      </ConsoleShell>,
    );
    await screen.findByText("Body content");

    const rail = screen.getByRole("navigation", { name: "Primary navigation" });
    const notesLink = within(rail).getByRole("link", { name: "Notes" });
    expect(notesLink.getAttribute("href")).toBe("/notes");
    expect(notesLink.getAttribute("aria-current")).toBe("page");

    expect(screen.getByRole("banner", { name: "Workspace top bar" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "All" }).getAttribute("aria-selected"))
      .toBe("true");
    expect(screen.getByRole("search", { name: "Global search" })).toBeTruthy();

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByText("Notes").getAttribute("aria-current"))
      .toBe("page");

    expect(screen.getByRole("main").textContent).toContain("Body content");
    expect(screen.getByRole("tab", { name: "Angee" })).toBeTruthy();
    expect(screen.getByText("No agent yet")).toBeTruthy();
    expect(screen.getByText("Set up your assistant")).toBeTruthy();
  });
});

// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider, containersFromChildren } from "../runtime";
import { createUiTestProviders } from "../testing";
import { ConsoleLayout } from "./ConsoleLayout";

const viewport = vi.hoisted(() => ({ mobile: false }));
// Refine resolves the trail from its router binding; the test harness has none, so the trail is given.
const trail = vi.hoisted(() => ({ crumbs: [] as { label: string; href?: string }[] }));
vi.mock("@refinedev/core", async (importOriginal) => ({
  ...await importOriginal<typeof import("@refinedev/core")>(),
  useBreadcrumb: () => ({ breadcrumbs: trail.crumbs }),
}));
vi.mock("../lib/use-media-query", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/use-media-query")>();
  return { ...actual, useMediaQuery: (query: string) => query === actual.MOBILE_VIEWPORT_QUERY ? viewport.mobile : !viewport.mobile };
});
afterEach(() => { cleanup(); viewport.mobile = false; });

const ui = createUiTestProviders({ refineResources: [
  { name: "menu:desk", list: "/desk", meta: { menuId: "desk", label: "Desk" } },
  { name: "menu:desk.notes", list: "/notes", show: "/notes/:id", meta: { menuId: "desk.notes", label: "Notes", parent: "menu:desk" } },
] });

describe("ConsoleLayout breadcrumb strip", () => {
  test("a menu destination shows no strip; the top bar already names the app and its menus", async () => {
    renderConsole(true, "/notes");
    const header = await screen.findByRole("banner", { name: "Workspace top bar" });
    expect(within(header).getByRole("navigation", { name: "Desk menu" })).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
    expect(screen.getByRole("main").closest(".console-grid")?.getAttribute("style"))
      .toContain("--breadcrumbbar-current-h: 0px");
  });

  test("nested navigation places the trail directly below TopBar and keeps app navigation in TopBar", async () => {
    renderConsole();
    const header = await screen.findByRole("banner", { name: "Workspace top bar" });
    const breadcrumb = await screen.findByRole("navigation", { name: "Breadcrumb" });
    const strip = breadcrumb.closest("[data-console-breadcrumbs]")!;
    expect(strip.previousElementSibling).toBe(header);
    expect(within(header).queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
    expect(within(header).getByRole("navigation", { name: "Desk menu" })).toBeTruthy();
    expect(strip.nextElementSibling?.classList.contains("area-control")).toBe(true);
  });

  test("hiding the chrome.breadcrumbs region hides the strip and releases its reserved route height", async () => {
    renderConsole(false);
    await screen.findByRole("banner", { name: "Workspace top bar" });
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();
    expect(screen.getByRole("main").closest(".console-grid")?.getAttribute("style"))
      .toContain("--breadcrumbbar-current-h: 0px");
    expect(screen.getByRole("navigation", { name: "Desk menu" })).toBeTruthy();
  });

  test("mobile retains the navigation drawer without a horizontally scrolling app menu", async () => {
    viewport.mobile = true;
    renderConsole();
    const header = await screen.findByRole("banner", { name: "Workspace top bar" });
    const menu = within(header).getByRole("navigation", { name: "Desk menu" });
    expect(menu.classList.contains("overflow-x-auto")).toBe(false);
    fireEvent.click(within(header).getByRole("button", { name: "Primary navigation" }));
    const drawer = await screen.findByRole("dialog", { name: "Primary navigation" });
    await waitFor(() => expect(within(drawer).getByRole("link", { name: "Desk" })).toBeTruthy());
    expect(within(drawer).queryByRole("link", { name: "Notes" })).toBeNull();
    fireEvent.keyDown(drawer, { key: "Escape" });
    await screen.findByRole("navigation", { name: "Breadcrumb" });
  });
});

function renderConsole(breadcrumb = true, path = "/notes/7") {
  trail.crumbs = [{ label: "Desk", href: "/desk" }, { label: "Notes", href: "/notes" }, ...(path === "/notes" ? [] : [{ label: "Show" }])];
  const root = createRootRoute({ component: () => <ui.Provider>
    <AppRuntimeProvider runtime={breadcrumb ? {} : {
      // The regions container as a layer leaves it once it hides the breadcrumb strip.
      containers: containersFromChildren([{ address: "shell#regions" }], { "shell#regions": { "chrome.app-menu": { content: null } } }),
    }}>
      <ConsoleLayout><div>Page</div></ConsoleLayout>
    </AppRuntimeProvider>
  </ui.Provider> });
  const router = createRouter({ routeTree: root.addChildren([
    createRoute({ getParentRoute: () => root, path: "/notes" }),
    createRoute({ getParentRoute: () => root, path: "/notes/$id" }),
  ]), history: createMemoryHistory({ initialEntries: [path] }) });
  render(<RouterProvider router={router} />);
}

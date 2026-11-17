// @vitest-environment happy-dom

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider } from "../runtime";
import { AppRail } from "./AppRail";
import { AppRailTree } from "./AppRailTree";
import { MenuTree, type ChromeMenuItem } from "./menu-tree";
import { APP_RAIL_PREFERENCES_KEY } from "./app-rail-preferences";

vi.mock("../lib/use-media-query", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/use-media-query")>()),
  useMediaQuery: () => true,
}));

const menus: readonly ChromeMenuItem[] = [{
  id: "notes", label: "Notes root", to: "/notes",
  children: [
    { id: "all", label: "All notes", to: "/notes", icon: "book" },
    { id: "archive", label: "Archive", to: "/notes/archive", icon: "archive" },
    { id: "desk", label: "Desk", app: true, to: "/desk", icon: "book", children: [
      { id: "desk.notes", label: "Desk notes", to: "/desk/notes" },
    ] },
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
    expect(within(nav).getByRole("link", { name: "Desk" }).getAttribute("href")).toBe("/desk");
    expect(within(nav).queryByRole("link", { name: "All notes" })).toBeNull();
    expect(within(nav).queryByRole("link", { name: "Archive" })).toBeNull();
    expect(within(nav).queryByRole("link", { name: "Desk notes" })).toBeNull();
    expect(within(nav).queryByRole("link", { name: "Notes root" })).toBeNull();
    expect(screen.queryByRole("button", { name: /choose|switch app|reorder/i })).toBeNull();
    expect(patchPreferences).not.toHaveBeenCalled();
  });

  test("flat AppRailTree does not repeat a childless root already represented by the brand", async () => {
    const tree = MenuTree.from([{ id: "notes", label: "Notes", to: "/notes" }]);
    const router = createRouter({
      history: createMemoryHistory({ initialEntries: ["/"] }),
      routeTree: createRootRoute({ component: () => <><AppRailTree scope="apps" flat roots={tree.roots} activeRootId="notes" /><span>Ready</span></> }),
    });
    render(<RouterProvider router={router} />);
    await screen.findByText("Ready");
    expect(screen.queryByRole("link", { name: "Notes" })).toBeNull();
    expect(screen.queryByRole("button", { name: /expand|collapse/i })).toBeNull();
  });
});


describe("branded multi-root rail", () => {
  test("the app chooser carries the brand mark and the caption the brand name", async () => {
    const router = createRouter({
      history: createMemoryHistory({ initialEntries: ["/"] }),
      routeTree: createRootRoute({ component: () => <AppRuntimeProvider runtime={{
        brand: { name: "Notebook", mark: "notebook-mark" },
        icons: { "notebook-mark": () => <svg data-testid="brand-mark" /> },
        userPreferences: { available: true, preferences: { [APP_RAIL_PREFERENCES_KEY]: { expanded: true } }, patchPreferences: vi.fn() },
      }}><AppRail menuItems={[
        { id: "notes", label: "Notes", to: "/notes", icon: "book" },
        { id: "files", label: "Files", to: "/files", icon: "files" },
      ]} /></AppRuntimeProvider> }),
    });
    render(<RouterProvider router={router} />);
    const chooser = await screen.findByRole("button", { name: "Switch app" });
    expect(within(chooser).getByTestId("brand-mark")).toBeTruthy();
    expect(screen.getByText("Notebook")).toBeTruthy();
    expect(screen.queryByText("Apps")).toBeNull();
  });
  test("without a brand the chooser keeps the framework glyph and the Apps caption", async () => {
    const router = createRouter({
      history: createMemoryHistory({ initialEntries: ["/"] }),
      routeTree: createRootRoute({ component: () => <AppRuntimeProvider runtime={{
        icons: { angee: () => <svg data-testid="angee-mark" /> },
        userPreferences: { available: true, preferences: { [APP_RAIL_PREFERENCES_KEY]: { expanded: true } }, patchPreferences: vi.fn() },
      }}><AppRail menuItems={[
        { id: "notes", label: "Notes", to: "/notes", icon: "book" },
        { id: "files", label: "Files", to: "/files", icon: "files" },
      ]} /></AppRuntimeProvider> }),
    });
    render(<RouterProvider router={router} />);
    const chooser = await screen.findByRole("button", { name: "Switch app" });
    expect(within(chooser).getByTestId("angee-mark")).toBeTruthy();
    expect(screen.getByText("Apps")).toBeTruthy();
  });
  test("a named multi-root app's stand-in brand (its first root's label and icon) is not shown as a brand", async () => {
    const router = createRouter({
      history: createMemoryHistory({ initialEntries: ["/"] }),
      routeTree: createRootRoute({ component: () => <AppRuntimeProvider runtime={{
        brand: { name: "Notes", mark: "book" }, brandFallback: true,
        icons: { angee: () => <svg data-testid="angee-mark" />, book: () => <svg data-testid="book-mark" /> },
        userPreferences: { available: true, preferences: { [APP_RAIL_PREFERENCES_KEY]: { expanded: true } }, patchPreferences: vi.fn() },
      }}><AppRail menuItems={[
        { id: "notes", label: "Notes", to: "/notes", icon: "book" },
        { id: "files", label: "Files", to: "/files", icon: "files" },
      ]} /></AppRuntimeProvider> }),
    });
    render(<RouterProvider router={router} />);
    const chooser = await screen.findByRole("button", { name: "Switch app" });
    expect(within(chooser).getByTestId("angee-mark")).toBeTruthy();
    expect(within(chooser).queryByTestId("book-mark")).toBeNull();
    expect(screen.getByText("Apps")).toBeTruthy();
  });
  test("a declared brand whose mark is not registered falls back to the framework glyph", async () => {
    const router = createRouter({
      history: createMemoryHistory({ initialEntries: ["/"] }),
      routeTree: createRootRoute({ component: () => <AppRuntimeProvider runtime={{
        brand: { name: "Notebook", mark: "unregistered-mark" },
        icons: { angee: () => <svg data-testid="angee-mark" /> },
        userPreferences: { available: true, preferences: { [APP_RAIL_PREFERENCES_KEY]: { expanded: true } }, patchPreferences: vi.fn() },
      }}><AppRail menuItems={[
        { id: "notes", label: "Notes", to: "/notes", icon: "book" },
        { id: "files", label: "Files", to: "/files", icon: "files" },
      ]} /></AppRuntimeProvider> }),
    });
    render(<RouterProvider router={router} />);
    const chooser = await screen.findByRole("button", { name: "Switch app" });
    expect(within(chooser).getByTestId("angee-mark")).toBeTruthy();
    expect(screen.getByText("Notebook")).toBeTruthy();
  });
});

test("the brand follows a query-bearing app target through the same chrome conversion", async () => {
  const root = createRootRoute({ component: () => <AppRuntimeProvider runtime={{ brand: { name: "Notebook", mark: "book" } }}>
    <AppRail menuItems={[{ id: "notes", label: "Notes", to: "/notes?preset=all" }]} />
  </AppRuntimeProvider> });
  const router = createRouter({ routeTree: root.addChildren([
    createRoute({ getParentRoute: () => root, path: "/" }), createRoute({ getParentRoute: () => root, path: "/notes" }),
  ]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  render(<RouterProvider router={router} />);
  fireEvent.click(await screen.findByRole("link", { name: "Notebook" }));
  await waitFor(() => expect(router.state.location.pathname).toBe("/notes"));
  expect(router.state.location.search).toEqual({ preset: "all" });
});

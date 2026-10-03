// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRuntimeProvider, type AppRuntime, type RuntimeComposition } from "../runtime";
import { AppMenu } from "./AppMenu";
import { MenuTree, type ChromeMenuItem } from "./menu-tree";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); window.sessionStorage.clear(); });

const menus: readonly ChromeMenuItem[] = [{ id: "suite", label: "Suite", to: "/suite", children: [
  { id: "suite.inbox", label: "Inbox", to: "/suite/inbox" },
  { id: "desk", label: "Desk", app: true, to: "/desk", children: [
    { id: "desk.notes", label: "Notes", to: "/desk/notes" },
    { id: "desk.open", label: "Open notes", to: "/desk/notes?preset=open" },
    { id: "desk.reports", label: "Reports", to: "/desk/reports", children: [
      { id: "desk.daily", label: "Daily", to: "/desk/reports/daily" },
      { id: "desk.archives", label: "Archives", children: [
        { id: "desk.year", label: "Year", to: "/desk/reports/year" },
      ] },
    ] },
    { id: "desk.hidden", label: "Hidden", hidden: true, to: "/desk/hidden" },
  ] },
] }];

describe("AppMenu", () => {
  test("moves narrow-width entries into More in order and restores them on resize", async () => {
    const resize = mockOverflow(1000);
    renderMenu("/desk");
    const nav = await screen.findByRole("navigation", { name: "Desk menu" });
    expect(within(nav).queryByRole("button", { name: "More" })).toBeNull();
    resize(300);
    expect(within(nav).getAllByRole("link").map((item) => item.textContent)).toEqual(["Desk", "Notes"]);
    fireEvent.click(within(nav).getByRole("button", { name: "More" }));
    const popup = await screen.findByRole("menu");
    expect(within(popup).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Open notes", "Reports", "Daily", "Year"]);
    expect(within(popup).getByRole("group", { name: "Reports" })).toBeTruthy();
    fireEvent.keyDown(popup, { key: "Escape" });
    resize(1000);
    expect(within(nav).queryByRole("button", { name: "More" })).toBeNull();
    expect(within(nav).getByRole("button", { name: "Reports" })).toBeTruthy();
    expect(nav.className).not.toContain("overflow-x-auto");
  });

  test("swaps the current trail's menu into the last visible slot, including a hidden deep link", async () => {
    mockOverflow(400);
    const items: readonly ChromeMenuItem[] = [{ id: "desk", label: "Desk", to: "/desk", children: [
      { id: "a", label: "A", to: "/a" },
      { id: "b", label: "B", to: "/b" },
      { id: "c", label: "C", to: "/c" },
      { id: "reports", label: "Reports", to: "/reports", children: [
        { id: "daily", label: "Daily", to: "/reports/daily" },
        { id: "secret", label: "Secret", hidden: true, to: "/secret" },
      ] },
    ] }];
    renderMenu("/secret", items);
    const nav = await screen.findByRole("navigation", { name: "Desk menu" });
    expect(within(nav).getByRole("button", { name: "Reports" }).getAttribute("aria-current")).toBe("true");
    expect(within(nav).queryByRole("link", { name: "B" })).toBeNull();
    fireEvent.click(within(nav).getByRole("button", { name: "More" }));
    expect(within(await screen.findByRole("menu")).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["B", "C"]);
  });

  test("developer removed markers overflow first and remain disabled in declaration order", async () => {
    const resize = mockOverflow(500);
    const composition: RuntimeComposition = {
      shell: { brand: null, perspective: null, provenance: {}, diagnostics: [] }, effective: { home: "/desk", confineTo: null },
      menus: { provenance: {}, hidden: [], unavailable: {}, diagnostics: [], removed: [
        { id: "old-a", label: "Old A", parent: "desk", by: "suite" },
        { id: "old-b", label: "Old B", parent: "desk", by: "suite" },
      ] },
    };
    renderMenu("/desk", menus, { composition, userPreferences: {
      available: true, preferences: { developerMode: true }, patchPreferences: async () => undefined,
    } });
    const nav = await screen.findByRole("navigation", { name: "Desk menu" });
    // Four developer-visible menus fit; only the removed markers spill at this width.
    resize(600);
    expect(within(nav).getByRole("link", { name: "Hidden (hidden)" })).toBeTruthy();
    expect(within(nav).queryByRole("link", { name: /removed by/ })).toBeNull();
    fireEvent.click(within(nav).getByRole("button", { name: "More" }));
    const entries = within(await screen.findByRole("menu")).getAllByRole("menuitem");
    expect(entries.map((item) => item.textContent)).toEqual(["Old A — removed by suite", "Old B — removed by suite"]);
    expect(entries.every((item) => item.getAttribute("aria-disabled") === "true")).toBe(true);
  });

  test.each(["/desk", "/desk/notes"])("with no room the app dropdown stays current and links to the app page first (%s)", async (path) => {
    mockOverflow(190);
    const router = renderMenu(path);
    const nav = await screen.findByRole("navigation", { name: "Desk menu" });
    expect(within(nav).queryByRole("link")).toBeNull();
    const trigger = within(nav).getByRole("button", { name: "Desk" });
    expect(trigger.getAttribute("data-current")).toBe("true");
    expect(trigger.getAttribute("aria-current")).toBe("true");
    fireEvent.click(trigger);
    const popup = await screen.findByRole("menu");
    expect(within(popup).getAllByRole("menuitem").map((item) => item.textContent))
      .toEqual(["Desk", "Notes", "Open notes", "Reports", "Daily", "Year"]);
    const appPage = within(popup).getByRole("menuitem", { name: "Desk" });
    expect(appPage.getAttribute("href")).toBe("/desk");
    if (path === "/desk/notes") {
      fireEvent.click(appPage);
      await waitFor(() => expect(router.state.location.pathname).toBe("/desk"));
    }
  });

  test("selects the nearest included app and renders its links with aria-current", async () => {
    const router = renderMenu("/desk/notes");
    const nav = await screen.findByRole("navigation", { name: "Desk menu" });
    expect(within(nav).getByRole("link", { name: "Desk" }).getAttribute("href")).toBe("/desk");
    const notes = within(nav).getByRole("link", { name: "Notes" });
    expect(notes.getAttribute("aria-current")).toBe("page");
    expect(notes.getAttribute("data-current")).toBe("true");
    expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
    expect(within(nav).queryByText("Hidden")).toBeNull();
    expect(within(nav).queryByText("Inbox")).toBeNull();
    fireEvent.click(within(nav).getByRole("link", { name: "Open notes" }));
    await waitFor(() => expect(router.state.location.search).toEqual({ preset: "open" }));
    expect(within(nav).getByRole("link", { name: "Open notes" }).getAttribute("aria-current")).toBe("page");
    expect(notes.getAttribute("aria-current")).toBeNull();
    expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
  });

  test("an aggregator shows only its own items", async () => {
    renderMenu("/suite/inbox");
    const nav = await screen.findByRole("navigation", { name: "Suite menu" });
    expect(within(nav).getAllByRole("link").map((item) => item.textContent)).toEqual(["Suite", "Inbox"]);
    expect(within(nav).queryByRole("button")).toBeNull();
  });

  test("keyboard opens a current dropdown; its own page comes first and deeper items are groups", async () => {
    const router = renderMenu("/desk/reports/daily");
    const trigger = await screen.findByRole("button", { name: "Reports" });
    expect(trigger.getAttribute("aria-current")).toBe("true");
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "ArrowDown" });
    const popup = await screen.findByRole("menu");
    const entries = within(popup).getAllByRole("menuitem");
    expect(entries.map((item) => item.textContent)).toEqual(["Reports", "Daily", "Year"]);
    expect(entries[1]?.getAttribute("aria-current")).toBe("page");
    expect(within(popup).getByRole("group", { name: "Archives" })).toBeTruthy();
    fireEvent.click(entries[2]!);
    await waitFor(() => expect(router.state.location.pathname).toBe("/desk/reports/year"));
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  });

  test("Settings selects its place and does not duplicate a page already targeted by a child", async () => {
    renderMenu("/operator/services", [{ id: "operator", label: "Operator", group: "platform", to: "/operator", children: [
      { id: "operator.overview", label: "Overview", to: "/operator" },
      { id: "operator.services", label: "Services", to: "/operator/services" },
    ] }]);
    const nav = await screen.findByRole("navigation", { name: "Settings menu" });
    expect(within(nav).getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("/operator");
    fireEvent.click(within(nav).getByRole("button", { name: "Operator" }));
    expect(within(await screen.findByRole("menu")).getAllByRole("menuitem").map((item) => item.textContent))
      .toEqual(["Overview", "Services"]);
  });

  test("route-less Settings items with one visible child link to it using the parent label", async () => {
    renderMenu("/tags", [{ id: "tags", label: "Tags", group: "platform", children: [
      { id: "tags.hidden", label: "Hidden", hidden: true, to: "/hidden" },
      { id: "tags.all", label: "All tags", to: "/tags" },
    ] }]);
    const nav = await screen.findByRole("navigation", { name: "Settings menu" });
    const tags = within(nav).getByRole("link", { name: "Tags" });
    expect(tags.getAttribute("href")).toBe("/tags");
    expect(tags.getAttribute("aria-current")).toBe("page");
    expect(within(nav).queryByRole("button", { name: "Tags" })).toBeNull();
    expect(within(nav).queryByText("All tags")).toBeNull();
  });

  test("More preserves the parent label and single-child destination of a route-less Settings item", async () => {
    mockOverflow(300);
    renderMenu("/operator", [
      { id: "operator", label: "Operator", group: "platform", to: "/operator" },
      { id: "tags", label: "Tags", group: "platform", children: [
        { id: "tags.hidden", label: "Hidden", hidden: true, to: "/hidden" },
        { id: "tags.all", label: "All tags", to: "/tags" },
      ] },
      { id: "audit", label: "Audit", group: "platform", to: "/audit" },
    ]);
    const nav = await screen.findByRole("navigation", { name: "Settings menu" });
    fireEvent.click(within(nav).getByRole("button", { name: "More" }));
    const popup = await screen.findByRole("menu");
    const tags = within(popup).getByRole("menuitem", { name: "Tags" });
    expect(tags.getAttribute("href")).toBe("/tags");
    expect(within(popup).queryByRole("group", { name: "Tags" })).toBeNull();
    expect(within(popup).queryByText("All tags")).toBeNull();
    expect(within(popup).getAllByRole("menuitem").map((item) => item.textContent)).toEqual(["Tags", "Audit"]);
  });

  test("navigation within Settings keeps its entries and ResizeObserver connected", async () => {
    const resize = mockOverflow(1000);
    const router = renderMenu("/operator", [{ id: "operator", label: "Operator", group: "platform", to: "/operator", children: [
      { id: "operator.overview", label: "Overview", to: "/operator" },
      { id: "operator.services", label: "Services", to: "/operator/services" },
    ] }]);
    await screen.findByRole("navigation", { name: "Settings menu" });
    resize.disconnect.mockClear();
    await act(() => router.navigate({ to: "/operator/services" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/operator/services"));
    expect(resize.disconnect).not.toHaveBeenCalled();
  });

  test("route-less items with several children remain dropdowns without an own-page entry", async () => {
    renderMenu("/tags", [{ id: "tags", label: "Tags", group: "platform", children: [
      { id: "tags.all", label: "All tags", to: "/tags" },
      { id: "tags.archived", label: "Archived tags", to: "/tags/archived" },
    ] }]);
    fireEvent.click(await screen.findByRole("button", { name: "Tags" }));
    expect(within(await screen.findByRole("menu")).getAllByRole("menuitem").map((entry) => entry.textContent))
      .toEqual(["All tags", "Archived tags"]);
  });

  test("a hidden included app selects its visible aggregator", async () => {
    renderMenu("/hidden", [{ id: "suite", label: "Suite", to: "/suite", children: [
      { id: "hidden", app: true, hidden: true, to: "/hidden" },
      { id: "suite.inbox", label: "Inbox", to: "/suite/inbox" },
    ] }]);
    const nav = await screen.findByRole("navigation", { name: "Suite menu" });
    expect(within(nav).getAllByRole("link").map((item) => item.textContent)).toEqual(["Suite", "Inbox"]);
    expect(nav.querySelector('[aria-current="page"]')).toBeNull();
  });

  test("an unowned route leaves the menu empty", async () => {
    renderMenu("/unowned");
    await waitFor(() => expect(screen.getByText("Page")).toBeTruthy());
    expect(screen.queryByRole("navigation")).toBeNull();
  });
});

function renderMenu(initial: string, items: readonly ChromeMenuItem[] = menus, runtime: Partial<AppRuntime> = {}) {
  const tree = MenuTree.from(items);
  const root = createRootRoute({ component: () => <AppRuntimeProvider runtime={runtime}><AppMenu menuItems={tree} /><Outlet /></AppRuntimeProvider> });
  const paths = new Set(["/unowned", ...[...tree.byId.values()].flatMap((item) => item.path ? [item.path] : [])]);
  const router = createRouter({
    routeTree: root.addChildren([...paths].map((path) => createRoute({ getParentRoute: () => root, path,
      validateSearch: (search) => search, component: () => <div>Page</div>,
    }))),
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  render(<RouterProvider router={router} />);
  return router;
}

/** Real DOM geometry is supplied by ResizeObserver; every intrinsic entry is 100px here. */
function mockOverflow(initialWidth: number) {
  let width = initialWidth;
  const observers = new Map<ResizeObserverCallback, Set<Element>>();
  const disconnect = vi.fn();
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    return { width: this.matches("nav") ? width : this.closest("[inert]") ? 100 : 0 } as DOMRect;
  });
  vi.stubGlobal("ResizeObserver", class {
    targets = new Set<Element>();
    constructor(readonly callback: ResizeObserverCallback) { observers.set(callback, this.targets); }
    observe(target: Element) { this.targets.add(target); }
    unobserve(target: Element) { this.targets.delete(target); }
    disconnect() { disconnect(); observers.delete(this.callback); }
  });
  return Object.assign((next: number) => act(() => {
    width = next;
    for (const [callback, targets] of observers) {
      callback([...targets].filter((target) => target.matches("nav")).map((target) => ({
        target, contentRect: { width } as DOMRectReadOnly,
        borderBoxSize: [], contentBoxSize: [], devicePixelContentBoxSize: [],
      })), {} as ResizeObserver);
    }
  }), { disconnect });
}

// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { afterEach, describe, expect, test } from "vitest";

import { AppMenu } from "./AppMenu";
import { MenuTree, type ChromeMenuItem } from "./menu-tree";

afterEach(cleanup);

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

function renderMenu(initial: string, items: readonly ChromeMenuItem[] = menus) {
  const tree = MenuTree.from(items);
  const root = createRootRoute({ component: () => <><AppMenu menuItems={tree} /><Outlet /></> });
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

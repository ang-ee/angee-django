// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Outlet, RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import { afterEach, describe, expect, test, vi } from "vitest";

import { AppRailTree, type AppRailTreeProps } from "./AppRailTree";
import { MenuTree } from "./menu-tree";
import { ChromePlaceProvider, useChromePlace } from "./refine-menu";

afterEach(cleanup);

// Aggregators include apps; an app's page/menu entries never carry app: true.
const projects = { id: "projects", label: "Projects", to: "/projects", children: [
  { id: "projects.inbox", label: "Inbox", to: "/projects/inbox" },
  { id: "boards", label: "Boards", app: true, to: "/boards", children: [
    { id: "boards.queue", label: "Queue", to: "/boards/queue" },
  ] },
] };

describe("AppRailTree", () => {
  test("roots show included apps only, and sub-apps show no further children", async () => {
    const tree = MenuTree.from([{ id: "suite", label: "Suite", to: "/suite", children: [
      { id: "suite.inbox", label: "Inbox", to: "/suite/inbox" },
      { id: "desk", label: "Desk", app: true, children: [
        { id: "desk.notes", label: "Notes", to: "/notes" },
        { id: "desk.inner", label: "Inner app", app: true, to: "/inner" },
      ] },
      { id: "hidden", label: "Hidden app", app: true, hidden: true, to: "/hidden" },
    ] }]);
    renderTree(tree, "/notes");
    const desk = await screen.findByRole("link", { name: "Desk" });
    expect(desk.getAttribute("href")).toBe("/notes");
    expect(desk.getAttribute("aria-current")).toBe("true");
    for (const name of ["Inbox", "Notes", "Inner app", "Hidden app"]) {
      expect(screen.queryByRole("link", { name })).toBeNull();
    }
    expect(screen.queryByRole("button", { name: "Collapse Desk" })).toBeNull();
  });

  test("follows an included app's preset through Router's full-href navigation", async () => {
    const tree = MenuTree.from([{ id: "suite", label: "Suite", to: "/suite", children: [
      { id: "desk", label: "Desk", app: true, to: "/desk/notes?preset=desk.open", children: [
        { id: "desk.notes", label: "Notes", to: "/desk/notes" },
      ] },
    ] }]);
    const router = renderTree(tree, "/suite");
    const desk = await screen.findByRole("link", { name: "Desk" });
    fireEvent.click(desk);
    await waitFor(() => expect(router.state.location.pathname).toBe("/desk/notes"));
    expect(router.state.location.search).toEqual({ preset: "desk.open" });
    expect(desk.getAttribute("data-current")).toBe("true");
    expect(desk.getAttribute("aria-current")).toBe("page");
    expect(screen.queryByRole("link", { name: "Notes" })).toBeNull();
  });

  test("controls its included-app accordion and includes badge metadata in its name", async () => {
    const tree = MenuTree.from([{ ...projects, badge: 3 }]);
    renderTree(tree, "/projects");
    const trigger = await screen.findByRole("button", { name: "Collapse Projects, 3 items" });
    const panelId = trigger.getAttribute("aria-controls");
    expect(panelId).toBeTruthy();
    expect(document.getElementById(panelId!)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Boards" })).toBeTruthy();
    fireEvent.click(trigger);
    expect(screen.getByRole("button", { name: "Expand Projects, 3 items" }).getAttribute("aria-expanded"))
      .toBe("false");
  });

  test("only the selected included app is current and its exact page toggles the rail", async () => {
    const tree = MenuTree.from([projects, { id: "notes", label: "Notes", to: "/notes" }]);
    const onActiveToggle = vi.fn();
    renderTree(tree, "/boards", { onActiveToggle });
    const parentLink = await screen.findByRole("link", { name: "Projects" });
    expect(parentLink.getAttribute("data-current")).toBe("false");
    expect(parentLink.getAttribute("aria-current")).toBeNull();
    const activeLink = screen.getByRole("link", { name: "Boards" });
    expect(activeLink.getAttribute("data-current")).toBe("true");
    expect(activeLink.getAttribute("aria-current")).toBe("page");
    expect(fireEvent.click(activeLink)).toBe(false);
    expect(onActiveToggle).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("link", { name: "Notes" }));
    expect(onActiveToggle).toHaveBeenCalledTimes(1);
  });

  test("opens a requested inactive root without marking it active", async () => {
    const tree = MenuTree.from([projects, {
      id: "notes", label: "Notes", to: "/notes", children: [
        { id: "writer", label: "Writer", app: true, to: "/writer", children: [
          { id: "writer.drafts", label: "Drafts", to: "/writer/drafts" },
        ] },
      ],
    }]);
    const router = renderTree(tree, "/boards", { defaultOpenRootId: "notes" });
    const primary = within(await screen.findByTestId("tree"));
    expect(primary.getByRole("link", { name: "Projects" }).getAttribute("data-current")).toBe("false");
    expect(primary.getByRole("link", { name: "Notes" }).getAttribute("data-current")).toBe("false");
    expect(primary.getByRole("button", { name: "Collapse Notes" })).toBeTruthy();
    expect(primary.getByRole("button", { name: "Expand Projects" })).toBeTruthy();
    const writer = primary.getByRole("link", { name: "Writer" });
    fireEvent.click(writer);
    await waitFor(() => expect(router.state.location.pathname).toBe("/writer"));
  });
});

function renderTree(tree: MenuTree, initial: string, props: Partial<AppRailTreeProps> = {}) {
  function MatchedTree() {
    const { match } = useChromePlace();
    return <div data-testid="tree"><AppRailTree scope="apps" roots={tree.railMenuItems()}
      activeRootId={match?.trail[0]?.id ?? null} selectedAppId={match?.trail[0]?.id}
      selectedSubAppId={match?.app?.parentNode ? match.app.id : undefined} pageId={match?.item.id}
      {...props} /></div>;
  }
  const root = createRootRoute({ component: () => <ChromePlaceProvider menuItems={tree}>
    <MatchedTree /><Outlet />
  </ChromePlaceProvider> });
  const paths = new Set([...tree.byId.values()].flatMap((item) => item.path ? [item.path] : []));
  const router = createRouter({ routeTree: root.addChildren([...paths].map((path) => createRoute({
    getParentRoute: () => root, path, validateSearch: (search) => search,
  }))), history: createMemoryHistory({ initialEntries: [initial] }) });
  render(<RouterProvider router={router} />);
  return router;
}

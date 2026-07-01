// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { useMemo, type ReactNode, type SVGProps } from "react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import { parseFlatSearch, stringifyFlatSearch } from "../create-app";
import { setThemePreference, storedThemePreference } from "@angee/ui/lib/theme";
import { ConsoleLayout } from "@angee/ui/layouts/ConsoleLayout";
import { ControlBand } from "@angee/ui/layouts/ControlBand";
import { useChatterContent } from "@angee/ui/communication/index";

vi.mock("@angee/logo-react", () => ({
  AngeeLogo: ({ bgColor: _bgColor, geometry: _geometry, preset: _preset, ...props }: SVGProps<SVGSVGElement> & {
    bgColor?: string | null;
    geometry?: string;
    preset?: string;
  }) => <svg {...props} data-testid="angee-logo" />,
}));

vi.mock("@refinedev/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@refinedev/core")>();
  return {
    ...actual,
    useBreadcrumb: () => ({ breadcrumbs: [{ label: "Notes" }] }),
    // Three apps: a domain app "Notes" (with two sections), a sibling domain app
    // "Ops", and a platform app "Admin" (with two sections). Refine owns menu
    // state; chrome renders the tree projected by `useMenu`.
    useMenu: () => ({
      defaultOpenKeys: [],
      selectedKey: "/menu:notes",
      menuItems: [
        {
          key: "/menu:notes",
          name: "menu:notes",
          identifier: "menu:notes",
          route: "/notes",
          meta: { menuId: "notes", label: "Notes", icon: "file" },
          label: "Notes",
          icon: "file",
          children: [
            {
              key: "/menu:notes/menu:notes.all",
              name: "menu:notes.all",
              identifier: "menu:notes.all",
              route: "/notes",
              meta: { menuId: "notes.all", label: "All notes", icon: "list" },
              label: "All notes",
              icon: "list",
              children: [],
            },
            {
              key: "/menu:notes/menu:notes.archive",
              name: "menu:notes.archive",
              identifier: "menu:notes.archive",
              route: "/notes/archive",
              meta: { menuId: "notes.archive", label: "Archived", icon: "archive" },
              label: "Archived",
              icon: "archive",
              children: [],
            },
          ],
        },
        {
          key: "/menu:ops",
          name: "menu:ops",
          identifier: "menu:ops",
          route: "/ops",
          meta: { menuId: "ops", label: "Ops", icon: "activity" },
          label: "Ops",
          icon: "activity",
          children: [],
        },
        {
          key: "/menu:admin",
          name: "menu:admin",
          identifier: "menu:admin",
          route: "/admin",
          meta: {
            menuId: "admin",
            label: "Admin",
            icon: "settings",
            group: "platform",
            sidebar: true,
          },
          label: "Admin",
          icon: "settings",
          children: [
            {
              key: "/menu:admin/menu:admin.overview",
              name: "menu:admin.overview",
              identifier: "menu:admin.overview",
              route: "/admin",
              meta: { menuId: "admin.overview", label: "Overview", icon: "home" },
              label: "Overview",
              icon: "home",
              children: [],
            },
            {
              key: "/menu:admin/menu:admin.settings",
              name: "menu:admin.settings",
              identifier: "menu:admin.settings",
              route: "/admin/settings",
              meta: { menuId: "admin.settings", label: "Settings", icon: "settings" },
              label: "Settings",
              icon: "settings",
              children: [],
            },
          ],
        },
      ],
    }),
  };
});

vi.mock("@angee/refine", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/refine")>();
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
  };
});

function renderInRouter(children: ReactNode, initialPath = "/notes") {
  const rootRoute = createRootRoute({
    component: () => <Outlet />,
  });
  const notesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/notes",
    component: () => <>{children}</>,
  });
  const archiveRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/notes/archive",
    component: () => null,
  });
  const opsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/ops",
    component: () => null,
  });
  const adminRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/admin",
    component: () => <>{children}</>,
  });
  const adminSettingsRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/admin/settings",
    component: () => null,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([
      notesRoute,
      archiveRoute,
      opsRoute,
      adminRoute,
      adminSettingsRoute,
    ]),
    history: createMemoryHistory({ initialEntries: [initialPath] }),
    parseSearch: parseFlatSearch,
    stringifySearch: stringifyFlatSearch,
  });

  return render(<RouterProvider router={router} />);
}

describe("ConsoleLayout", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
  });
  afterEach(() => {
    cleanup();
    setThemePreference("system");
    document.documentElement.removeAttribute("data-theme");
  });

  test("composes rail navigation, top chrome, breadcrumbs, content, and chatter", async () => {
    renderInRouter(
      <ConsoleLayout>
        <section aria-label="Page body">Body content</section>
      </ConsoleLayout>,
    );
    await screen.findByText("Body content");

    // The rail is the app switcher: one icon per app (Notes is active here, Ops
    // is the sibling).
    const rail = screen.getByRole("navigation", { name: "Primary navigation" });
    const notesLink = within(rail).getByRole("link", { name: "Notes" });
    expect(notesLink.getAttribute("href")).toBe("/notes");
    expect(notesLink.getAttribute("aria-current")).toBe("page");
    expect(within(rail).getByRole("link", { name: "Ops" })).toBeTruthy();
    // The platform app clusters in the rail's bottom zone, still a rail link.
    expect(within(rail).getByRole("link", { name: "Admin" })).toBeTruthy();

    // The top bar navigates within the active app: it lists Notes' sections and
    // never the sibling app's entry.
    const topBar = screen.getByRole("banner", { name: "Workspace top bar" });
    expect(within(topBar).getByText("All notes")).toBeTruthy();
    expect(within(topBar).getByText("Archived")).toBeTruthy();
    expect(within(topBar).queryByText("Ops")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Open command palette" }),
    ).toBeTruthy();
    expect(
      within(topBar).getByRole("button", { name: "Switch to dark mode" }),
    ).toBeTruthy();

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByText("Notes").getAttribute("aria-current"))
      .toBe("page");

    expect(screen.getByRole("main").textContent).toContain("Body content");
    expect(screen.getByRole("tab", { name: "Agents" })).toBeTruthy();
    expect(screen.getByText("No agent yet")).toBeTruthy();
    expect(screen.getByText("Set up your assistant")).toBeTruthy();
  });

  test("renders a sidebar app's sections in both the sub-nav and the top bar", async () => {
    renderInRouter(
      <ConsoleLayout>
        <section aria-label="Page body">Admin body</section>
      </ConsoleLayout>,
      "/admin",
    );
    await screen.findByText("Admin body");

    // An app opting into the sidebar (`sidebar: true`) shows its sections in the
    // settings-style left sub-nav.
    const subNav = screen.getByRole("navigation", { name: "Section navigation" });
    expect(within(subNav).getByRole("link", { name: "Overview" })).toBeTruthy();
    expect(within(subNav).getByRole("link", { name: "Settings" })).toBeTruthy();

    // …and the top bar keeps them too — the sidebar is additive, not a swap.
    const topBar = screen.getByRole("banner", { name: "Workspace top bar" });
    expect(within(topBar).getByText("Overview")).toBeTruthy();
    expect(within(topBar).getByText("Settings")).toBeTruthy();
  });

  test("portals a ControlBand into the area-control row", async () => {
    const { container } = renderInRouter(
      <ConsoleLayout>
        <ControlBand>
          <button type="button">Band control</button>
        </ControlBand>
        <section aria-label="Page body">Body content</section>
      </ConsoleLayout>,
    );
    await screen.findByText("Body content");

    const control = container.querySelector(".area-control");
    const button = await screen.findByRole("button", { name: "Band control" });
    // Lands in the layout's control row, not inline in the content area.
    expect(control?.contains(button)).toBe(true);
    expect(screen.getByRole("main").contains(button)).toBe(false);
  });

  test("toggles the document theme from the top bar", async () => {
    renderInRouter(
      <ConsoleLayout>
        <section aria-label="Page body">Body content</section>
      </ConsoleLayout>,
    );
    await screen.findByText("Body content");

    fireEvent.click(screen.getByRole("button", { name: "Switch to dark mode" }));

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(storedThemePreference()).toBe("dark");
    expect(
      screen.getByRole("button", { name: "Switch to light mode" }),
    ).toBeTruthy();
  });

  test("leaves the area-control row empty when no ControlBand is mounted", async () => {
    const { container } = renderInRouter(
      <ConsoleLayout>
        <section aria-label="Page body">Body content</section>
      </ConsoleLayout>,
    );
    await screen.findByText("Body content");

    // Empty host → the auto-height grid row collapses to zero (no grey band).
    expect(container.querySelector(".area-control")?.children.length).toBe(0);
  });

  test("renders the band inline when there is no layout above", () => {
    render(
      <ControlBand>
        <button type="button">Standalone control</button>
      </ControlBand>,
    );
    expect(screen.getByRole("button", { name: "Standalone control" })).toBeTruthy();
  });

  test("lets page content publish chatter tabs through context", async () => {
    renderInRouter(
      <ConsoleLayout>
        <ChatterPublisher />
        <section aria-label="Page body">Body content</section>
      </ConsoleLayout>,
    );
    await screen.findByText("Body content");

    fireEvent.click(screen.getByRole("tab", { name: "Activity 2" }));
    expect(screen.getByText("Revision one")).toBeTruthy();
  });
});

function ChatterPublisher(): null {
  const tabs = useMemo(
    () => [
      {
        id: "agents",
        label: "Agents",
        children: <p>No agent yet</p>,
      },
      {
        id: "activity",
        label: "Activity",
        count: 2,
        children: <p>Revision one</p>,
      },
    ],
    [],
  );
  const content = useMemo(() => ({ tabs }), [tabs]);
  useChatterContent(content);
  return null;
}

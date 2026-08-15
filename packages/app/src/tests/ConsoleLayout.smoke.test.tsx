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
import { baseIcons } from "@angee/ui/chrome/icon-registry";
import { ConsoleLayout } from "@angee/ui/layouts/ConsoleLayout";
import { ControlBand } from "@angee/ui/layouts/ControlBand";
import { PrimaryPanePublisher } from "@angee/ui/layouts/primary-pane-context";
import { Statusline, StatusSegment } from "@angee/ui/layouts/Statusline";
import { useChatterContent } from "@angee/ui/communication/index";
import { AppRuntimeProvider, type AppRuntime } from "@angee/ui/runtime";

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

let largeViewport = true;

function runtimeForConsoleTest(): Partial<AppRuntime> {
  return {
    icons: baseIcons,
    auth: {
      user: {
        id: "user_1",
        name: "Ada Lovelace",
        email: "ada@example.com",
      },
      status: "authenticated" as const,
      hasRole: () => false,
    },
    logoutAction: {
      logout: vi.fn(async () => true),
      fetching: false,
      error: null,
    },
    userPreferences: {
      preferences: {
        "chrome.rail": {
          order: [],
          defaultItemId: null,
          expanded: true,
        },
      },
      setPreferences: async () => undefined,
    },
  };
}

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

  return render(
    <AppRuntimeProvider runtime={runtimeForConsoleTest()}>
      <RouterProvider router={router} />
    </AppRuntimeProvider>,
  );
}

describe("ConsoleLayout", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
    window.matchMedia = vi.fn((query: string): MediaQueryList => ({
      matches: query === "(min-width: 64rem)" && largeViewport,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(() => false),
    }));
  });
  afterEach(() => {
    cleanup();
    largeViewport = true;
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

    // At lg the rail defaults to its in-place tree, with no sibling nav pane.
    const rail = screen.getByRole("navigation", { name: "Primary navigation" });
    const notesLink = within(rail).getByRole("link", { name: "Notes" });
    expect(notesLink.getAttribute("href")).toBe("/notes");
    expect(notesLink.getAttribute("data-active")).toBe("true");
    expect(within(rail).getByRole("link", { name: "Ops" })).toBeTruthy();
    const notesDisclosure = within(rail).getByRole("button", {
      name: "Collapse Notes",
    });
    expect(notesDisclosure.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(notesDisclosure.getAttribute("aria-controls")!))
      .toBeTruthy();
    expect(within(rail).getByRole("link", { name: "All notes" })).toBeTruthy();

    // The rail is one scrolling list: domains, separator, then Settings.
    const settingsLink = within(rail).getByRole("link", { name: "Settings" });
    expect(settingsLink.closest('[data-rail-list="true"]')).toBe(rail);
    expect(rail.className).toContain("overflow-y-auto");
    expect(rail.querySelector(".overflow-y-auto")).toBeNull();
    expect(rail.querySelector("[data-rail-zone]")).toBeNull();
    expect(
      notesLink.compareDocumentPosition(settingsLink)
        & Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
    expect(within(rail).queryByRole("link", { name: "Admin" })).toBeNull();

    // The top bar owns global actions and breadcrumbs. Section navigation stays
    // in the rail rather than being duplicated here.
    const topBar = screen.getByRole("banner", { name: "Workspace top bar" });
    expect(within(topBar).queryByText("All notes")).toBeNull();
    expect(within(topBar).queryByText("Archived")).toBeNull();
    expect(within(topBar).queryByText("Ops")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Open command palette" }),
    ).toBeTruthy();
    expect(within(topBar).getByRole("button", { name: "User menu" })).toBeTruthy();
    expect(within(topBar).queryByRole("button", { name: "Notifications" }))
      .toBeNull();
    expect(within(topBar).queryByRole("button", { name: "Help" })).toBeNull();
    // The expansion toggle is pinned at the rail's foot — outside the top bar
    // and outside the scrolling list, so it can never scroll away.
    const railToggle = await screen.findByRole("button", {
      name: "Collapse app navigation",
    });
    expect(railToggle.getAttribute("aria-expanded")).toBe("true");
    expect(topBar.contains(railToggle)).toBe(false);
    expect(rail.contains(railToggle)).toBe(false);
    expect(railToggle.closest("aside")).toBe(rail.closest("aside"));
    expect(within(topBar).queryByRole("button", {
      name: "Collapse primary panel",
    })).toBeNull();

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(topBar.contains(breadcrumb)).toBe(true);
    expect(within(breadcrumb).getByText("Notes").getAttribute("aria-current"))
      .toBe("page");

    expect(screen.getByRole("main").textContent).toContain("Body content");
    expect(screen.getByRole("tab", { name: "Agents" })).toBeTruthy();
    expect(screen.getByText("No agent yet")).toBeTruthy();
    expect(screen.getByText("Set up your assistant")).toBeTruthy();

    fireEvent.click(railToggle);
    expect(await screen.findByRole("button", {
      name: "Expand app navigation",
    })).toBeTruthy();
    expect(within(rail).queryByRole("link", { name: "All notes" })).toBeNull();
    expect(within(rail).getByRole("link", { name: "Settings" })).toBeTruthy();

    // A second click on the already-active app icon re-expands the rail.
    fireEvent.click(within(rail).getByRole("link", { name: "Notes" }));
    expect(await within(rail).findByRole("link", { name: "All notes" }))
      .toBeTruthy();
  });

  test("swaps the expanded rail tree to the Settings place", async () => {
    renderInRouter(
      <ConsoleLayout>
        <section aria-label="Page body">Admin body</section>
      </ConsoleLayout>,
      "/admin",
    );
    await screen.findByText("Admin body");

    const rail = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(within(rail).getByRole("heading", { name: "Settings" })).toBeTruthy();
    expect(within(rail).getByRole("link", { name: "Back" }).getAttribute("href"))
      .toBe("/");
    expect(within(rail).getByRole("button", { name: "Collapse Admin" })
      .getAttribute("aria-expanded")).toBe("true");
    expect(within(rail).getByRole("link", { name: "Overview" })).toBeTruthy();
    expect(within(rail).getAllByRole("link", { name: "Settings" })).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "Switch app" }));
    const chooser = await screen.findByRole("dialog", { name: "Switch app" });
    expect(within(chooser).getByRole("link", { name: "Settings" })
      .getAttribute("aria-current")).toBe("page");
    fireEvent.click(within(chooser).getByRole("button", {
      name: "Close app chooser",
    }));

    expect(screen.queryByRole("navigation", { name: "Section navigation" }))
      .toBeNull();
    const topBar = screen.getByRole("banner", { name: "Workspace top bar" });
    expect(within(topBar).queryByText("Overview")).toBeNull();
    expect(within(topBar).queryByText("Settings")).toBeNull();
    const settingsRailToggle = await screen.findByRole("button", {
      name: "Collapse app navigation",
    });
    expect(topBar.contains(settingsRailToggle)).toBe(false);
    expect(settingsRailToggle.closest("aside")).toBe(rail.closest("aside"));
    expect(within(topBar).queryByRole("button", {
      name: "Collapse primary panel",
    })).toBeNull();
  });

  test("renders a page-published explorer as the only primary pane", async () => {
    renderInRouter(
      <ConsoleLayout>
        <TestPrimaryPanePublisher />
        <section aria-label="Page body">Body with explorer</section>
      </ConsoleLayout>,
    );
    await screen.findByText("Body with explorer");

    expect(screen.getByRole("navigation", { name: "Context explorer" }))
      .toBeTruthy();
    expect(within(screen.getByRole("navigation", { name: "Primary navigation" }))
      .getByRole("link", { name: "All notes" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Collapse primary panel" }))
      .toBeTruthy();
  });

  test("keeps an explicit expanded preference collapsed below lg", async () => {
    largeViewport = false;
    renderInRouter(
      <ConsoleLayout>
        <section aria-label="Page body">Compact body</section>
      </ConsoleLayout>,
    );
    await screen.findByText("Compact body");

    const rail = screen.getByRole("navigation", { name: "Primary navigation" });
    expect(within(rail).queryByRole("link", { name: "All notes" })).toBeNull();
    const topBar = screen.getByRole("banner", { name: "Workspace top bar" });
    expect(within(topBar).queryByRole("button", {
      name: "Collapse app navigation",
    })).toBeNull();
    expect(within(topBar).queryByRole("button", {
      name: "Expand app navigation",
    })).toBeNull();
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

  test("uses browser scrolling for content and pins the statusline host", async () => {
    const { container } = renderInRouter(
      <ConsoleLayout>
        <section aria-label="Page body">Tall body</section>
        <Statusline>
          <StatusSegment>Ready</StatusSegment>
        </Statusline>
      </ConsoleLayout>,
    );
    await screen.findByText("Tall body");

    expect(screen.getByRole("main").className).toBe("console-browser-scroll-main");
    const statusHost = container.querySelector(".area-status");
    expect(statusHost?.className).toContain("console-statusline-host");
    expect(statusHost?.textContent).toContain("Ready");
  });

  test("toggles the document theme from the user menu", async () => {
    renderInRouter(
      <ConsoleLayout>
        <section aria-label="Page body">Body content</section>
      </ConsoleLayout>,
    );
    await screen.findByText("Body content");

    fireEvent.click(screen.getByRole("button", { name: "User menu" }));
    fireEvent.click(await screen.findByRole("menuitem", {
      name: "Switch to dark mode",
    }));

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(storedThemePreference()).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: "User menu" }));
    expect(await screen.findByRole("menuitem", { name: "Switch to light mode" }))
      .toBeTruthy();
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

function TestPrimaryPanePublisher() {
  const node = useMemo(
    () => <nav aria-label="Context explorer">Explorer</nav>,
    [],
  );
  return <PrimaryPanePublisher node={node} />;
}

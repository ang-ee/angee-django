// @vitest-environment happy-dom

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
  type SVGProps,
} from "react";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import { parseFlatSearch, stringifyFlatSearch } from "../create-app";
import { CORE_CONTAINERS } from "../core-containers";
import { setThemePreference, storedThemePreference } from "@angee/ui/lib/theme";
import { baseIcons } from "@angee/ui/chrome/icon-registry";
import { ConsoleLayout } from "@angee/ui/layouts/ConsoleLayout";
import { ControlBand } from "@angee/ui/layouts/ControlBand";
import { PrimaryPanePublisher } from "@angee/ui/layouts/primary-pane-context";
import { Statusline, StatusSegment } from "@angee/ui/layouts/Statusline";
import { useChatterContent } from "@angee/ui/communication/index";
import {
  AppRuntimeProvider,
  containersFromChildren,
  type AppRuntime,
  type RuntimeUserPreferences,
  type RuntimeUserPreferencesPatch,
} from "@angee/ui/runtime";

vi.mock("@angee/logo-react", async (importOriginal) => {
  const { PRESETS } = await importOriginal<typeof import("@angee/logo-react")>();
  return {
    AngeeLogo: ({ bgColor: _bgColor, geometry: _geometry, preset: _preset, ...props }: SVGProps<SVGSVGElement> & {
      bgColor?: string | null;
      geometry?: string;
      preset?: string;
    }) => <svg {...props} data-testid="angee-logo" />,
    AngeeLogoCube: () => <span data-testid="angee-logo-cube" />,
    PRESETS,
  };
});

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

function runtimeForConsoleTest(
  preferences: RuntimeUserPreferences,
  patchPreferences: (apply: RuntimeUserPreferencesPatch) => Promise<void>,
): Partial<AppRuntime> {
  return {
    icons: baseIcons,
    containers: containersFromChildren(CORE_CONTAINERS, {}),
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
      available: true,
      preferences,
      patchPreferences,
    },
  };
}

function renderInRouter(
  children: ReactNode,
  initialPath = "/notes",
  runtime?: Partial<AppRuntime>,
) {
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
    <ConsoleTestRuntime runtime={runtime}>
      <RouterProvider router={router} />
    </ConsoleTestRuntime>,
  );
}

function ConsoleTestRuntime({
  children,
  runtime,
}: {
  children: ReactNode;
  runtime?: Partial<AppRuntime>;
}): ReactNode {
  const [preferences, setPreferences] = useState<RuntimeUserPreferences>({
    "chrome.rail": {
      order: [],
      defaultItemId: null,
      expanded: true,
    },
  });
  const patchPreferences = useCallback(
    async (apply: RuntimeUserPreferencesPatch): Promise<void> => {
      setPreferences((current) => apply(current));
    },
    [],
  );
  const resolvedRuntime = useMemo(
    () => ({
      ...runtimeForConsoleTest(preferences, patchPreferences),
      ...runtime,
    }),
    [patchPreferences, preferences, runtime],
  );
  return <AppRuntimeProvider runtime={resolvedRuntime}>{children}</AppRuntimeProvider>;
}

describe("ConsoleLayout", () => {
  beforeAll(() => {
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

  test("composes console chrome without an empty chatter pane on a non-record page", async () => {
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
    expect(notesLink.getAttribute("data-current")).toBe("true");
    expect(notesLink.getAttribute("aria-current")).toBe("true");
    expect(within(rail).getByRole("link", { name: "Ops" })).toBeTruthy();
    expect(within(rail).queryByRole("button", { name: "Collapse Notes" })).toBeNull();
    expect(within(rail).queryByRole("link", { name: "All notes" })).toBeNull();

    // The rail is one scrolling list for the active domain place. Settings is
    // selected through the app chooser rather than duplicated in this tree.
    expect(rail.firstElementChild?.className).toContain("h-full");
    expect(rail.querySelector('[data-orientation="vertical"]')).toBeTruthy();
    expect(rail.querySelector("[data-rail-zone]")).toBeNull();
    expect(within(rail).queryByRole("link", { name: "Settings" })).toBeNull();
    expect(within(rail).queryByRole("link", { name: "Admin" })).toBeNull();

    // The top bar owns the selected app's menus and the existing global actions.
    const topBar = screen.getByRole("banner", { name: "Workspace top bar" });
    expect(within(topBar).getByRole("link", { name: "All notes" }).getAttribute("aria-current")).toBe("page");
    expect(within(topBar).getByRole("link", { name: "Archived" })).toBeTruthy();
    expect(within(topBar).queryByText("Ops")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Open command palette" }),
    ).toBeTruthy();
    expect(within(topBar).getByRole("button", { name: "User menu" })).toBeTruthy();
    expect(within(topBar).queryByRole("button", { name: "Notifications" }))
      .toBeNull();
    expect(within(topBar).queryByRole("button", { name: "Help" })).toBeNull();
    // Header and footer controls stay outside the top bar and scrolling list.
    const railToggles = await screen.findAllByRole("button", {
      name: "Collapse app navigation",
    });
    expect(railToggles).toHaveLength(2);
    for (const toggle of railToggles) {
      expect(toggle.getAttribute("aria-expanded")).toBe("true");
      expect(topBar.contains(toggle)).toBe(false);
      expect(rail.contains(toggle)).toBe(false);
      expect(toggle.closest("aside")).toBe(rail.closest("aside"));
    }
    expect(within(topBar).queryByRole("button", {
      name: "Collapse primary panel",
    })).toBeNull();

    // A menu destination needs no trail: the top bar names the app and its menus.
    expect(screen.queryByRole("navigation", { name: "Breadcrumb" })).toBeNull();

    expect(screen.getByRole("main").textContent).toContain("Body content");
    expect(screen.queryByRole("tab", { name: "Comments" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Activity" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Agents" })).toBeNull();

    fireEvent.click(railToggles[1]!);
    expect(await screen.findByRole("button", {
      name: "Expand app navigation",
    })).toBeTruthy();
    expect(within(rail).queryByRole("link", { name: "All notes" })).toBeNull();
    expect(within(rail).queryByRole("link", { name: "Settings" })).toBeNull();

    // A second click on the already-active app icon re-expands the rail.
    fireEvent.click(within(rail).getByRole("link", { name: "Notes" }));
    expect(await screen.findAllByRole("button", { name: "Collapse app navigation" })).toHaveLength(2);
    expect(within(rail).queryByRole("link", { name: "All notes" })).toBeNull();
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
    expect(within(rail).getByRole("link", { name: "Admin" })).toBeTruthy();
    expect(within(rail).queryByRole("link", { name: "Overview" })).toBeNull();
    const appMenu = screen.getByRole("navigation", { name: "Settings menu" });
    fireEvent.click(within(appMenu).getByRole("button", { name: "Admin" }));
    const settingsMenu = await screen.findByRole("menu");
    expect(within(settingsMenu).getByRole("menuitem", { name: "Overview" })).toBeTruthy();
    expect(within(settingsMenu).getByRole("menuitem", { name: "Settings" })).toBeTruthy();
    fireEvent.keyDown(settingsMenu, { key: "Escape" });

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
    expect(within(topBar).getByRole("navigation", { name: "Settings menu" })).toBeTruthy();
    const settingsRailToggles = await screen.findAllByRole("button", {
      name: "Collapse app navigation",
    });
    expect(settingsRailToggles).toHaveLength(2);
    for (const toggle of settingsRailToggles) {
      expect(topBar.contains(toggle)).toBe(false);
      expect(toggle.closest("aside")).toBe(rail.closest("aside"));
    }
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
      .queryByRole("link", { name: "All notes" })).toBeNull();
    const topBar = screen.getByRole("banner", { name: "Workspace top bar" });
    expect(within(topBar).getByRole("link", { name: "All notes" })).toBeTruthy();
    expect(within(topBar).getByRole("button", { name: "Collapse primary panel" })).toBeTruthy();
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

  test("keeps a delayed console notice above an already-mounted control band", async () => {
    let revealNotice: () => void = () => undefined;
    const noticeRequested = new Promise<void>((resolve) => {
      revealNotice = resolve;
    });
    function DelayedNotice() {
      const [visible, setVisible] = useState(false);
      useEffect(() => {
        void noticeRequested.then(() => setVisible(true));
      }, []);
      return visible ? <div>Restart notice</div> : null;
    }
    const runtime: Partial<AppRuntime> = {
      containers: containersFromChildren(CORE_CONTAINERS, {
        "shell#notices": { "test.delayed-notice": { content: <DelayedNotice /> } },
      }),
    };
    const { container } = renderInRouter(
      <ConsoleLayout>
        <ControlBand><button type="button">Page filters</button></ControlBand>
      </ConsoleLayout>,
      "/notes",
      runtime,
    );

    const filters = await screen.findByRole("button", { name: "Page filters" });
    expect(screen.queryByText("Restart notice")).toBeNull();
    await act(async () => {
      revealNotice();
      await noticeRequested;
    });
    const notice = await screen.findByText("Restart notice");
    const host = container.querySelector(".area-control");
    expect(host?.contains(notice)).toBe(true);
    expect(host?.contains(filters)).toBe(true);
    expect(notice.compareDocumentPosition(filters) & Node.DOCUMENT_POSITION_FOLLOWING)
      .not.toBe(0);
  });

  test("keeps the primary pane's controls local when the main collection opens a record", async () => {
    function Page() {
      const [reading, setReading] = useState(false);
      const primary = useMemo(() => (
        <section aria-label="Finder"><ControlBand><button>Finder filters</button></ControlBand></section>
      ), []);
      return <>
        <PrimaryPanePublisher node={primary} />
        {reading ? <p>Message record</p> : <ControlBand><button>Result filters</button></ControlBand>}
        <button onClick={() => setReading(true)}>Read record</button>
      </>;
    }
    const { container } = renderInRouter(<ConsoleLayout><Page /></ConsoleLayout>);
    const finder = await screen.findByRole("button", { name: "Finder filters" });
    const result = await screen.findByRole("button", { name: "Result filters" });
    const host = container.querySelector(".area-control");
    expect(host?.contains(result)).toBe(true);
    expect(screen.getByRole("region", { name: "Finder" }).contains(finder)).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Read record" }));
    await screen.findByText("Message record");
    expect(host?.querySelector("[data-console-controls]")?.childNodes.length).toBe(0);
    expect(screen.getByRole("button", { name: "Finder filters" })).toBe(finder);
    expect(screen.getByRole("region", { name: "Finder" }).contains(finder)).toBe(true);
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

    expect(screen.getByRole("main").className).toBe("console-content-main");
    expect(container.querySelector(".area-content")?.className).toContain("h-full");
    const statusHost = container.querySelector(".area-status");
    expect(statusHost?.className).toContain("console-statusline-host");
    expect(statusHost?.textContent).toContain("Ready");
  });

  test("keeps the page band mounted when a chatter details form changes", async () => {
    const { container } = renderInRouter(
      <ConsoleLayout>
        <ChatterFormBandPublisher />
      </ConsoleLayout>,
    );
    const mainAction = await screen.findByRole("button", { name: "Main file action" });
    const host = container.querySelector(".area-control");
    expect(host?.contains(mainAction)).toBe(true);

    fireEvent.click(await screen.findByRole("tab", { name: "File details" }));
    const saveA = await screen.findByRole("button", { name: "Save file a" });
    expect(host?.contains(saveA)).toBe(false);
    expect(host?.contains(mainAction)).toBe(true);
    expect(screen.getByRole("button", { name: "Main file action" })).toBe(mainAction);

    fireEvent.click(screen.getByRole("button", { name: "Next file" }));
    await screen.findByRole("button", { name: "Save file b" });
    expect(host?.contains(mainAction)).toBe(true);
    expect(screen.getByRole("button", { name: "Main file action" })).toBe(mainAction);

    fireEvent.click(screen.getByRole("tab", { name: "Comments" }));
    expect(host?.contains(mainAction)).toBe(true);
    expect(screen.getByRole("button", { name: "Main file action" })).toBe(mainAction);
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

    // Empty display:contents hosts contribute no box, so the auto-height grid
    // row still collapses to zero without a grey band.
    const control = container.querySelector(".area-control");
    expect(control?.textContent).toBe("");
    expect(control?.querySelector("[data-console-notices]")?.className).toContain("contents");
    expect(control?.querySelector("[data-console-controls]")?.className).toContain("contents");
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

function ChatterFormBandPublisher(): ReactNode {
  const [record, setRecord] = useState("a");
  const content = useMemo(() => ({
    tabs: [{
      id: "details",
      label: "File details",
      children: (
        <section aria-label="File details form">
          <ControlBand><button type="button">Save file {record}</button></ControlBand>
          <button type="button" onClick={() => setRecord("b")}>Next file</button>
        </section>
      ),
    }, { id: "comments", label: "Comments", children: <span>File discussion</span> }],
  }), [record]);
  useChatterContent(content);
  return <ControlBand><button type="button">Main file action</button></ControlBand>;
}

function TestPrimaryPanePublisher() {
  const node = useMemo(
    () => <nav aria-label="Context explorer">Explorer</nav>,
    [],
  );
  return <PrimaryPanePublisher node={node} />;
}

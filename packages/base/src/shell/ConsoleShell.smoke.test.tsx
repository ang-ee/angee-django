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

import { parseFlatSearch, stringifyFlatSearch } from "../createApp";
import { ConsoleShell } from "./ConsoleShell";
import { ControlBand } from "./ControlBand";
import { useChatterContent } from "../communication";

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
    // Two apps: "Notes" (with two sections) and a sibling "Ops". The rail
    // switches apps; the top bar shows the active app's sections.
    useMenus: () => [
      {
        id: "notes",
        label: "Notes",
        to: "/notes",
        icon: "file",
        children: [
          { id: "notes.all", label: "All notes", to: "/notes", icon: "list" },
          { id: "notes.archive", label: "Archived", to: "/notes/archive", icon: "archive" },
        ],
      },
      { id: "ops", label: "Ops", to: "/ops", icon: "activity" },
    ],
  };
});

function renderInRouter(children: ReactNode) {
  const rootRoute = createRootRoute({
    component: () => <Outlet />,
  });
  const notesRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/notes",
    staticData: {
      chrome: {
        title: "Notes",
        icon: "file",
        breadcrumbs: [{ label: "Notes" }],
      },
    },
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
  const router = createRouter({
    routeTree: rootRoute.addChildren([notesRoute, archiveRoute, opsRoute]),
    history: createMemoryHistory({ initialEntries: ["/notes"] }),
    parseSearch: parseFlatSearch,
    stringifySearch: stringifyFlatSearch,
  });

  return render(<RouterProvider router={router} />);
}

describe("ConsoleShell", () => {
  beforeAll(() => {
    Element.prototype.getAnimations ??= () => [];
  });
  afterEach(() => cleanup());

  test("composes rail navigation, top chrome, breadcrumbs, content, and chatter", async () => {
    renderInRouter(
      <ConsoleShell title="Notes" icon="file">
        <section aria-label="Page body">Body content</section>
      </ConsoleShell>,
    );
    await screen.findByText("Body content");

    // The rail is the app switcher: one icon per app (Notes is active here, Ops
    // is the sibling).
    const rail = screen.getByRole("navigation", { name: "Primary navigation" });
    const notesLink = within(rail).getByRole("link", { name: "Notes" });
    expect(notesLink.getAttribute("href")).toBe("/notes");
    expect(notesLink.getAttribute("aria-current")).toBe("page");
    expect(within(rail).getByRole("link", { name: "Ops" })).toBeTruthy();

    // The top bar navigates within the active app: it lists Notes' sections and
    // never the sibling app's entry.
    const topBar = screen.getByRole("banner", { name: "Workspace top bar" });
    expect(within(topBar).getByText("All notes")).toBeTruthy();
    expect(within(topBar).getByText("Archived")).toBeTruthy();
    expect(within(topBar).queryByText("Ops")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Open command palette" }),
    ).toBeTruthy();

    const breadcrumb = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(breadcrumb).getByText("Notes").getAttribute("aria-current"))
      .toBe("page");

    expect(screen.getByRole("main").textContent).toContain("Body content");
    expect(screen.getByRole("tab", { name: "Angee" })).toBeTruthy();
    expect(screen.getByText("No agent yet")).toBeTruthy();
    expect(screen.getByText("Set up your assistant")).toBeTruthy();
  });

  test("portals a ControlBand into the area-control row", async () => {
    const { container } = renderInRouter(
      <ConsoleShell title="Notes" icon="file">
        <ControlBand>
          <button type="button">Band control</button>
        </ControlBand>
        <section aria-label="Page body">Body content</section>
      </ConsoleShell>,
    );
    await screen.findByText("Body content");

    const control = container.querySelector(".area-control");
    const button = await screen.findByRole("button", { name: "Band control" });
    // Lands in the shell's control row, not inline in the content area.
    expect(control?.contains(button)).toBe(true);
    expect(screen.getByRole("main").contains(button)).toBe(false);
  });

  test("leaves the area-control row empty when no ControlBand is mounted", async () => {
    const { container } = renderInRouter(
      <ConsoleShell title="Notes" icon="file">
        <section aria-label="Page body">Body content</section>
      </ConsoleShell>,
    );
    await screen.findByText("Body content");

    // Empty host → the auto-height grid row collapses to zero (no grey band).
    expect(container.querySelector(".area-control")?.children.length).toBe(0);
  });

  test("renders the band inline when there is no shell above", () => {
    render(
      <ControlBand>
        <button type="button">Standalone control</button>
      </ControlBand>,
    );
    expect(screen.getByRole("button", { name: "Standalone control" })).toBeTruthy();
  });

  test("lets page content publish chatter tabs through context", async () => {
    renderInRouter(
      <ConsoleShell title="Notes" icon="file">
        <ChatterPublisher />
        <section aria-label="Page body">Body content</section>
      </ConsoleShell>,
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
        id: "angee",
        label: "Angee",
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

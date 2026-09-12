// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { afterEach, beforeAll, describe, expect, test, vi } from "vitest";

import { baseIcons } from "../chrome/icon-registry";
import {
  AppRuntimeProvider,
  type AppRuntime,
  type ChatterViewContext,
} from "../runtime";
import { Chatter, useChatterHasContent } from "./Chatter";
import { ChatterProvider, useChatterContent, type ChatterContent } from "./chatter-context";

beforeAll(() => {
  Element.prototype.getAnimations ??= () => [];
});

afterEach(() => cleanup());

describe("Chatter", () => {
  test("only renders the agents tab when an addon contributes it", async () => {
    renderChatter({});

    expect(await screen.findByRole("tab", { name: "Comments" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Agents" })).toBeNull();

    cleanup();
    renderChatter({
      chatter: [{
        id: "agents",
        label: "Agents",
        icon: "agent",
        render: () => <span>Agents-owned empty state</span>,
      }],
    });

    expect(await screen.findByRole("tab", { name: "Agents" })).toBeTruthy();
    expect(screen.getByText("Agents-owned empty state")).toBeTruthy();
  });

  test("prefers reactive contribution counts while preserving static counts", async () => {
    renderChatter({
      chatterRoutes: [
        {
          name: "notes.record",
          path: "/records/$id",
          viewType: "notes/record",
          modelLabel: "notes.Note",
          recordParam: "id",
        },
      ],
      chatter: [
        {
          id: "comments",
          label: "Comments",
          icon: "comments",
          count: 2,
          useCount: useCommentsCount,
          render: (context) => <span>{context.view.sqid}</span>,
        },
        {
          id: "activity",
          label: "Activity",
          icon: "activity",
          count: 5,
          render: () => <span>Activity panel</span>,
        },
      ],
    });

    expect(await screen.findByRole("tab", { name: /Activity\s*5/ })).toBeTruthy();
    const commentsTab = await screen.findByRole("tab", {
      name: /Comments\s*7/,
    });
    expect(commentsTab.textContent).not.toContain("2");
  });

  test("scopes before rendering while canonical models include MTI subtypes", async () => {
    const hiddenCount = vi.fn(() => 9);
    const hiddenRender = vi.fn(() => <span>Wrong model</span>);
    const blockedRender = vi.fn(() => <span>Blocked</span>);
    renderChatter({
      chatterRoutes: [
        {
          name: "notes.record",
          path: "/records/$id",
          viewType: "notes/record",
          modelLabel: "crm.Vip",
          canonicalLabel: "parties.Party",
          recordParam: "id",
        },
      ],
      chatter: [
        {
          id: "wrong-model",
          model: "notes.Note",
          label: "Wrong model",
          useCount: hiddenCount,
          render: hiddenRender,
        },
        {
          id: "blocked",
          model: "parties.Party",
          when: () => false,
          label: "Blocked",
          render: blockedRender,
        },
        {
          id: "history",
          model: "parties.Party",
          when: (context) => context.view.kind === "record",
          label: "History",
          render: (context) => <span>History for {context.view.sqid}</span>,
        },
      ],
    });

    const historyTab = await screen.findByRole("tab", { name: "History" });
    fireEvent.click(historyTab);
    expect(screen.getByText("History for rec_1")).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "Wrong model" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Blocked" })).toBeNull();
    expect(hiddenCount).not.toHaveBeenCalled();
    expect(hiddenRender).not.toHaveBeenCalled();
    expect(blockedRender).not.toHaveBeenCalled();
  });
});

function useCommentsCount(
  context: ChatterViewContext,
): number | undefined {
  if (context.route?.modelLabel !== "notes.Note") return undefined;
  if (context.view.kind !== "record") return undefined;
  return context.view.sqid === "rec_1" ? 7 : undefined;
}

describe("useChatterHasContent", () => {
  test("has nothing to show on a view with no record selected", async () => {
    // The board defect: the aside's default tabs are about a record, so with
    // nothing selected the rail is empty and sits over the page -- on a board it
    // covers a lane, and the cards under it cannot be grabbed at all.
    expect(await renderHasContent({ path: "/queues/$queueId/board" })).toBe(false);
    cleanup();
    expect(await renderHasContent({ path: "/tasks" })).toBe(false);
  });

  test("has something to show once a record is selected", async () => {
    expect(
      await renderHasContent({
        path: "/records/$id",
        initialEntry: "/records/rec_1",
        runtime: {
          chatterRoutes: [
            { name: "notes.record", path: "/records/$id", viewType: "record", recordParam: "id" },
          ],
        },
      }),
    ).toBe(true);
  });

  test("keeps an aside a page or an addon asked for", async () => {
    // A view an addon contributes to deliberately, and a page that publishes its
    // own tabs, both keep their aside with no record in sight.
    expect(
      await renderHasContent({
        path: "/tasks",
        runtime: {
          chatter: [{ id: "agents", label: "Agents", render: () => <span>Agents</span> }],
        },
      }),
    ).toBe(true);
    cleanup();
    expect(
      await renderHasContent({
        path: "/tasks",
        content: { tabs: [{ id: "details", label: "Details", children: <span>Details</span> }] },
      }),
    ).toBe(true);
  });
});

async function renderHasContent({
  path,
  initialEntry,
  runtime = {},
  content,
}: {
  path: string;
  initialEntry?: string;
  runtime?: Partial<AppRuntime>;
  content?: ChatterContent;
}): Promise<boolean> {
  const probed: { value?: boolean } = {};
  function Probe(): null {
    probed.value = useChatterHasContent();
    return null;
  }
  function Publisher(): null {
    useChatterContent(content ?? null);
    return null;
  }
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const route = createRoute({
    getParentRoute: () => rootRoute,
    path,
    component: () => (
      <AppRuntimeProvider runtime={{ icons: baseIcons, ...runtime }}>
        <ChatterProvider>
          {content ? <Publisher /> : null}
          <Probe />
        </ChatterProvider>
      </AppRuntimeProvider>
    ),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([route]),
    history: createMemoryHistory({
      initialEntries: [initialEntry ?? path.replace(/\$[^/]+/g, "x")],
    }),
  });
  render(<RouterProvider router={router} />);
  await waitFor(() => expect(probed.value).not.toBeUndefined());
  return probed.value as boolean;
}

function renderChatter(runtime: Partial<AppRuntime>): void {
  const rootRoute = createRootRoute({ component: () => <Outlet /> });
  const recordRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/records/$id",
    component: () => (
      <AppRuntimeProvider runtime={{ icons: baseIcons, ...runtime }}>
        <ChatterProvider defaultTab="agents">
          <Chatter />
        </ChatterProvider>
      </AppRuntimeProvider>
    ),
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([recordRoute]),
    history: createMemoryHistory({ initialEntries: ["/records/rec_1"] }),
  });

  render(<RouterProvider router={router} />);
}

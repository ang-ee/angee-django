// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import { afterEach, beforeAll, describe, expect, test } from "vitest";

import { baseIcons } from "../chrome/icon-registry";
import {
  AppRuntimeProvider,
  type AppRuntime,
  type ChatterViewContext,
} from "../runtime";
import { Chatter } from "./Chatter";
import { ChatterProvider } from "./chatter-context";

beforeAll(() => {
  Element.prototype.getAnimations ??= () => [];
});

afterEach(() => cleanup());

describe("Chatter", () => {
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
});

function useCommentsCount(
  context: ChatterViewContext,
): number | undefined {
  if (context.route?.modelLabel !== "notes.Note") return undefined;
  if (context.view.kind !== "record") return undefined;
  return context.view.sqid === "rec_1" ? 7 : undefined;
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

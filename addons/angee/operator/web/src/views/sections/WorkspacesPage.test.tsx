// @vitest-environment happy-dom

import { render, screen } from "@testing-library/react";
import {
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import type { ReactElement } from "react";
import { describe, expect, test } from "vitest";

import { WorkspaceSources } from "./WorkspacesPage";

function renderInRouter(ui: ReactElement): void {
  const rootRoute = createRootRoute();
  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => ui,
  });
  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute]),
    history: createMemoryHistory({ initialEntries: ["/"] }),
    parseSearch: () => ({}),
    stringifySearch: () => "",
  });
  render(<RouterProvider router={router} />);
}

describe("WorkspaceSources", () => {
  test("renders workspace source status through the shared rows view", async () => {
    renderInRouter(
      <WorkspaceSources
        sources={[
          {
            slot: "main",
            source: "notes",
            kind: "git",
            mode: "workspace",
            branch: "workspace/demo",
            ref: null,
            subpath: null,
            path: "/workspace",
            exists: true,
            state: "ready",
            currentRef: null,
            dirty: false,
            upstream: null,
            ahead: 2,
            behind: 1,
            pushed: true,
            unpushedReason: null,
            error: null,
          },
        ]}
        title="Sources"
      />,
    );

    expect(await screen.findByText("Sources")).toBeTruthy();
    expect(screen.getByText("notes")).toBeTruthy();
    expect(screen.getByText("+2 / -1")).toBeTruthy();
    expect(screen.getByText("/workspace")).toBeTruthy();
  });
});

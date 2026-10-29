// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { createRouteHref } from "@angee/ui/runtime";

const platformMocks = vi.hoisted(() => ({
  search: {} as Record<string, unknown>,
  navigate: vi.fn(),
  routeHref: vi.fn(),
  usePlatformModelGraph: vi.fn(),
}));

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => platformMocks.navigate,
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  PrimaryPanePublisher: ({ node }: { node: ReactNode }) => <>{node}</>,
  GraphView: ({ className, onNodeClick }: {
    className?: string;
    onNodeClick?: (node: { id: string }) => void;
  }) => <button type="button" data-testid="graph-view" className={className}
    onClick={() => onNodeClick?.({ id: "operator.task" })} />,
  useRouteSearch: () => platformMocks.search,
  useRouteHref: () => platformMocks.routeHref,
}));

vi.mock("../lib/explorer", () => ({
  PLATFORM_GRAPH_PAGE_SIZE: 50,
  usePlatformModelGraph: platformMocks.usePlatformModelGraph,
}));

import { GraphPage } from "./GraphPage";
import platform from "../index";

const models = [
  { label: "iam.user", verbose_name: "User", addon_label: "IAM", relation_count: 103 },
  { label: "operator.task", verbose_name: "Task", addon_label: "Operator", relation_count: 1 },
];

beforeEach(() => {
  platformMocks.search = {};
  platformMocks.navigate.mockClear();
  platformMocks.routeHref.mockReset();
  const routeHref = createRouteHref(platform.routes ?? []);
  platformMocks.routeHref.mockImplementation(routeHref);
  Object.assign(platformMocks.routeHref, { maybe: routeHref.maybe });
  platformMocks.usePlatformModelGraph.mockReturnValue({
    models, model: models[0], nodes: [], edges: [], relationPage: 1, relationTotal: 103,
    isFetching: false, error: null,
  });
});

afterEach(() => cleanup());

function lastSearch(): Record<string, unknown> {
  const call = platformMocks.navigate.mock.lastCall?.[0] as {
    search: (previous: Record<string, unknown>) => Record<string, unknown>;
  };
  return call.search(platformMocks.search);
}

describe("GraphPage", () => {
  test("offers model details separately from graph recentering", () => {
    platformMocks.search = { model: "iam.user", relationPage: "2", other: "keep" };
    render(<GraphPage />);
    expect(screen.getByRole("link", { name: "iam.user" }).getAttribute("href"))
      .toBe("/platform/models/iam.user");
    expect(screen.getByTestId("graph-view").className).toContain("flex-1");
    fireEvent.click(screen.getByTestId("graph-view"));
    expect(lastSearch()).toEqual({ model: "operator.task", relationPage: undefined, other: "keep" });
  });

  test("search filters the navigator without replacing the URL selection", () => {
    platformMocks.search = { model: "iam.user" };
    render(<GraphPage />);
    fireEvent.change(screen.getByPlaceholderText("Search models…"), { target: { value: "Operator" } });
    expect(screen.queryByRole("treeitem", { name: /iam.user/ })).toBeNull();
    expect(screen.getByText("operator.task")).toBeTruthy();
    expect(screen.getByRole("link", { name: "iam.user" })).toBeTruthy();
    expect(platformMocks.navigate).not.toHaveBeenCalled();
    expect(platformMocks.usePlatformModelGraph).toHaveBeenLastCalledWith({ model: "iam.user", relationPage: 1 });
  });

  test("exposes the relationship total and navigates native pager pages", () => {
    platformMocks.search = { model: "iam.user", other: "keep" };
    render(<GraphPage />);
    expect(screen.getByText("1-50 / 103 relationships")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(lastSearch()).toEqual({ model: "iam.user", relationPage: "2", other: "keep" });
  });

  test("keeps loading and error states out of the graph", () => {
    platformMocks.usePlatformModelGraph.mockReturnValue({
      models: [], isFetching: true, error: null,
    });
    const view = render(<GraphPage />);
    expect(screen.queryByTestId("graph-view")).toBeNull();
    platformMocks.usePlatformModelGraph.mockReturnValue({
      models: [], isFetching: false, error: new Error("Unavailable"),
    });
    view.rerender(<GraphPage />);
    expect(screen.getByText("Unavailable")).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
  });
});

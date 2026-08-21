// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { createRouteHref } from "@angee/ui/runtime";

const platformMocks = vi.hoisted(() => ({
  modelScope: null as string | null,
  navigate: vi.fn(),
  routeHref: vi.fn(),
  usePlatformModelGraph: vi.fn(),
}));

vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => platformMocks.navigate,
}));

vi.mock("nuqs", () => ({
  parseAsString: {},
  useQueryState: () => [platformMocks.modelScope],
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  GraphView: ({
    className,
    onNodeClick,
  }: {
    className?: string;
    onNodeClick?: (node: { id: string }) => void;
  }) => (
    <button
      type="button"
      data-testid="graph-view"
      className={className}
      onClick={() => onNodeClick?.({ id: "notes.Note" })}
    />
  ),
  useRouteHref: () => platformMocks.routeHref,
}));

vi.mock("../lib/explorer", () => ({
  usePlatformModelGraph: platformMocks.usePlatformModelGraph,
}));

import { GraphPage } from "./GraphPage";
import platform from "../index";

beforeEach(() => {
  platformMocks.modelScope = null;
  platformMocks.navigate.mockClear();
  platformMocks.routeHref.mockReset();
  const routeHref = createRouteHref(platform.routes ?? []);
  platformMocks.routeHref.mockImplementation(routeHref);
  Object.assign(platformMocks.routeHref, { maybe: routeHref.maybe });
  platformMocks.usePlatformModelGraph.mockReturnValue({
    nodes: [],
    edges: [],
    error: null,
  });
});

afterEach(() => cleanup());

describe("GraphPage", () => {
  test("declares a concrete console canvas height for React Flow", () => {
    render(<GraphPage />);

    const graph = screen.getByTestId("graph-view");

    expect(graph.parentElement?.className).toContain("console-route-viewport");
    expect(graph.className).toContain("console-route-canvas");
  });

  test("opens graph nodes through the composed model-detail route", () => {
    render(<GraphPage />);

    screen.getByTestId("graph-view").click();

    expect(platformMocks.routeHref).toHaveBeenCalledWith(
      "platform.models.record",
      { id: "notes.Note" },
    );
    expect(platformMocks.navigate).toHaveBeenCalledWith({
      to: "/platform/models/notes.Note",
    });
  });
});

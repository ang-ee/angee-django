// @vitest-environment happy-dom

import { cleanup, render, renderHook, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import {
  RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter, } from "@tanstack/react-router";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { ListColumn } from "@angee/ui";

import type { OperatorSnapshotResult } from "../../data/transport";
import type { OperatorSnapshot } from "../../data/types";
import { daemonRowsByName, type DaemonRow } from "./daemon-rows";
import { OperatorRowsList, useOperatorRows } from "./operator-rows";

const transportMocks = vi.hoisted(() => ({
  sections: [] as unknown[],
  snapshotResult: null as OperatorSnapshotResult | null,
}));

vi.mock("../../data/transport", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../data/transport")>();
  return {
    ...actual,
    useOperatorSnapshot: (sections: unknown) => {
      transportMocks.sections.push(sections);
      if (!transportMocks.snapshotResult) {
        throw new Error("Missing mocked operator snapshot.");
      }
      return transportMocks.snapshotResult;
    },
  };
});

afterEach(() => cleanup());

beforeEach(() => {
  transportMocks.sections = [];
  transportMocks.snapshotResult = snapshotResult();
});

interface Service extends Record<string, unknown> {
  name: string;
  runtime: string;
}

type ServiceRow = DaemonRow<Service>;

const columns: readonly ListColumn<ServiceRow>[] = [
  { field: "name" },
  { field: "runtime" },
];

describe("OperatorRowsList", () => {
  test("selects rows from an operator snapshot and renders the shared rows view", async () => {
    renderInRouter(
      <OperatorRowsList<ServiceRow>
        scope="local"
        sections={{ services: true }}
        selectRows={(snapshot) => daemonRowsByName(snapshot.services)}
        columns={columns}
      />,
    );

    expect(await screen.findByText("api")).toBeTruthy();
    expect(screen.getByText("process")).toBeTruthy();
    expect(transportMocks.sections).toEqual([{ services: true }]);
  });
});

describe("useOperatorRows", () => {
  test("returns loading state without rows before the first snapshot", () => {
    transportMocks.snapshotResult = snapshotResult({
      snapshot: null,
      result: { fetching: true },
    });

    const { result } = renderHook(() =>
      useOperatorRows({ services: true }, (snapshot) =>
        daemonRowsByName(snapshot.services),
      ),
    );

    expect(result.current.rows).toEqual([]);
    expect(result.current.fetching).toBe(true);
    expect(result.current.error).toBeNull();
  });

  test("forwards first-load errors when no snapshot exists", () => {
    const error = new Error("daemon offline");
    transportMocks.snapshotResult = snapshotResult({
      snapshot: null,
      result: { error: error as OperatorSnapshotResult["result"]["error"] },
    });

    const { result } = renderHook(() =>
      useOperatorRows({ services: true }, (snapshot) =>
        daemonRowsByName(snapshot.services),
      ),
    );

    expect(result.current.rows).toEqual([]);
    expect(result.current.error).toBe(error);
  });

  test("keeps stale snapshot rows and suppresses transient query errors", () => {
    transportMocks.snapshotResult = snapshotResult({
      result: {
        error: new Error(
          "temporary network error",
        ) as OperatorSnapshotResult["result"]["error"],
      },
    });

    const { result } = renderHook(() =>
      useOperatorRows({ services: true }, (snapshot) =>
        daemonRowsByName(snapshot.services),
      ),
    );

    expect(result.current.rows.map((row) => row.name)).toEqual(["api"]);
    expect(result.current.error).toBeNull();
  });

  test("preserves snapshot refetch for action sections", () => {
    const refetch = vi.fn();
    transportMocks.snapshotResult = snapshotResult({ refetch });

    const { result } = renderHook(() =>
      useOperatorRows({ operations: true }, (snapshot) =>
        daemonRowsByName(snapshot.jobs),
      ),
    );

    result.current.refetch();
    expect(refetch).toHaveBeenCalledOnce();
  });
});

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

function snapshotResult({
  snapshot = defaultSnapshot(),
  result = {},
  refetch = vi.fn(),
}: {
  snapshot?: OperatorSnapshot | null;
  result?: Partial<OperatorSnapshotResult["result"]>;
  refetch?: OperatorSnapshotResult["refetch"];
} = {}): OperatorSnapshotResult {
  return {
    result: {
      fetching: false,
      stale: false,
      data: undefined,
      error: undefined,
      operation: undefined,
      extensions: undefined,
      hasNext: false,
      ...result,
    } as OperatorSnapshotResult["result"],
    snapshot,
    refetch,
  };
}

function defaultSnapshot(): OperatorSnapshot {
  return {
    health: null,
    stack: null,
    services: [{ name: "api", runtime: "process" }] as never,
    jobs: [],
    sources: [],
    workspaces: [],
    templates: [],
    secrets: [],
    gitOps: null,
  } satisfies OperatorSnapshot;
}

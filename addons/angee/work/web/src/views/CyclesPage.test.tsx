// @vitest-environment happy-dom

import { cleanup, render, screen } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  childMatches: [] as unknown[],
}));

vi.mock("@tanstack/react-router", () => ({
  Outlet: () => <div data-testid="child-route" />,
  useChildMatches: () => mocks.childMatches,
}));

vi.mock("@angee/ui", () => ({
  Column: () => null,
  ErrorBanner: () => null,
  List: ({ children }: { children?: React.ReactNode }) => (
    <div data-testid="cycles-list">{children}</div>
  ),
  Page: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  PageBody: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  PageHeader: () => null,
  useRouteParam: () => "que_eng",
  useRouteHref: () => () => "/work/queues/que_eng/cycles/cyc_7",
}));

vi.mock("../context", () => ({
  useQueueContext: () => ({ data: { work_queues_by_pk: { name: "Engineering" } } }),
}));
vi.mock("../cycle-actions", () => ({
  useCycleRowActions: () => ({ rowActions: [], dialog: null }),
}));
vi.mock("../i18n", () => ({ useWorkT: () => (key: string) => key }));

import { CyclesPage } from "./CyclesPage";

beforeEach(() => {
  mocks.childMatches = [];
});
afterEach(cleanup);

describe("CyclesPage", () => {
  test("shows the cycles list when no cycle is open", () => {
    render(<CyclesPage />);
    expect(screen.getByTestId("cycles-list")).toBeTruthy();
    expect(screen.queryByTestId("child-route")).toBeNull();
  });

  test("stands aside for the cycle board route", () => {
    // The cycle board is a record route under this one and carries its own
    // component, so this page has to yield or the router renders the list again
    // -- which is what made "Open Cycle 2" look like it did nothing.
    mocks.childMatches = [{ routeId: "/work/queues/$queueId/cycles/$id" }];
    render(<CyclesPage />);
    expect(screen.getByTestId("child-route")).toBeTruthy();
    expect(screen.queryByTestId("cycles-list")).toBeNull();
  });
});

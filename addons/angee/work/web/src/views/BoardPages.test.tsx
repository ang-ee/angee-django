// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listProps: null as Record<string, unknown> | null,
  surfaceProps: null as Record<string, unknown> | null,
  params: { queueId: "que_eng", id: "cyc_7" } as Record<string, string>,
}));

vi.mock("@angee/projects", () => ({
  TASK_MODEL: "projects.Task",
  TaskBoardSurface: (
    props: Record<string, unknown> & { children?: React.ReactNode },
  ) => {
    mocks.surfaceProps = props;
    return <>{props.children}</>;
  },
}));

vi.mock("@angee/ui", () => ({
  Column: () => null,
  ErrorBanner: () => null,
  List: (props: Record<string, unknown> & { children?: React.ReactNode }) => {
    mocks.listProps = props;
    return <>{props.children}</>;
  },
  Page: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  PageBody: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  PageHeader: () => null,
  useRouteParam: (name: string) => mocks.params[name],
}));

vi.mock("../context", () => ({
  useQueueContext: () => ({
    data: {
      work_queues_by_pk: { name: "Engineering", estimate_scale: "FIBONACCI" },
    },
  }),
  useCycleContext: () => ({
    data: {
      work_cycles_by_pk: {
        id: "cyc_7",
        name: "Cycle 7",
        starts_on: "2026-08-17",
        completed_at: null,
      },
    },
  }),
}));

vi.mock("../cycle-actions", () => ({
  CycleCloseControl: () => null,
}));

vi.mock("../i18n", () => ({
  useWorkT: () => (key: string) => key,
}));

vi.mock("../task-work", () => ({
  WorkTaskCard: () => null,
}));

import { CycleBoardPage } from "./CycleBoardPage";
import { QueueBoardPage } from "./QueueBoardPage";

beforeEach(() => {
  mocks.listProps = null;
  mocks.surfaceProps = null;
  mocks.params = { queueId: "que_eng", id: "cyc_7" };
});

afterEach(cleanup);

describe("work board stage lanes", () => {
  test("keeps triage and duplicate stages out of both planning boards", () => {
    // System stages are excluded by the LANE filters only: `stage` is an ID
    // comparison on the wire, so a nested stage.category baseFilter is not
    // expressible — rows in system stages simply have no lane to render in.
    const queue = render(<QueueBoardPage />);
    expectBoardStageScope(mocks.listProps, {
      queue: { exact: "que_eng" },
    });
    queue.unmount();

    render(<CycleBoardPage />);
    expectBoardStageScope(mocks.listProps, {
      queue: { exact: "que_eng" },
      cycle: { exact: "cyc_7" },
    });
  });
});

test("keeps identity-compared board props stable across renders", () => {
  // The collection surface compares these by identity, so a fresh object each
  // render re-runs its effects and grouped-scope work for a board that has not
  // changed -- the amplifier behind the board's update-depth errors.
  for (const Board of [QueueBoardPage, CycleBoardPage]) {
    const view = render(<Board />);
    const first = {
      baseFilter: mocks.listProps?.baseFilter,
      laneSource: mocks.listProps?.laneSource,
      createDefaults: mocks.surfaceProps?.createDefaults,
    };
    expect(first.baseFilter).toBeDefined();
    expect(first.laneSource).toBeDefined();
    expect(first.createDefaults).toBeDefined();

    view.rerender(<Board />);
    expect(mocks.listProps?.baseFilter).toBe(first.baseFilter);
    expect(mocks.listProps?.laneSource).toBe(first.laneSource);
    expect(mocks.surfaceProps?.createDefaults).toBe(first.createDefaults);
    view.unmount();
  }
});

function expectBoardStageScope(
  props: Record<string, unknown> | null,
  baseFilter: Record<string, unknown>,
): void {
  expect(props?.baseFilter).toEqual(baseFilter);
  expect(props?.laneSource).toEqual({
    field: "stage",
    rankField: "sort_order",
    filters: [
      { field: "queue", operator: "eq", value: "que_eng" },
      { field: "category", operator: "ne", value: "triage" },
      { field: "category", operator: "ne", value: "duplicate" },
    ],
    sorters: [{ field: "position", order: "asc" }],
  });
}

// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import * as React from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  listProps: null as Record<string, unknown> | null,
  params: { queueId: "que_eng", id: "cyc_7" } as Record<string, string>,
}));

vi.mock("@angee/projects", () => ({
  useTaskFormDeclaration: () => null,
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
  ResourceList: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  useRouteHref: () => () => "/projects/tasks/task",
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
  useParams: () => mocks.params,
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

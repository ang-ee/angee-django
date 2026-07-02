// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { ChatterViewContext } from "@angee/ui/runtime";
import type { RecordActivityRow } from "./documents";

const mocks = vi.hoisted(() => ({
  threadData: undefined as unknown,
  mutateCalls: [] as Array<{ op: string; vars: Record<string, unknown> }>,
  useAuthoredQuery: vi.fn(),
}));

function operationName(document: unknown): string {
  const definitions = (document as { definitions?: Array<{ name?: { value?: string } }> })
    .definitions;
  return definitions?.[0]?.name?.value ?? "";
}

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useNamespaceT:
      (_namespace: string, messages: Record<string, string>) =>
      (key: string) =>
        messages[key] ?? key,
  };
});

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: mocks.useAuthoredQuery,
  useAuthoredMutation: (document: unknown) => {
    const op = operationName(document);
    const mutate = vi.fn(async (vars: Record<string, unknown>) => {
      mocks.mutateCalls.push({ op, vars });
      return {};
    });
    return [mutate, { fetching: false }];
  },
}));

import { RecordActivityPane } from "./RecordActivityPane";

function activity(overrides: Partial<RecordActivityRow> = {}): RecordActivityRow {
  return {
    id: "act_1",
    activity_type: "todo",
    summary: "Follow up call",
    note: "",
    due_date: "2026-07-10",
    completed_at: null,
    feedback: "",
    status: "TODO",
    state: "planned",
    user: { id: "usr_1", username: "ada", display_name: "Ada Lovelace" },
    ...overrides,
  } as unknown as RecordActivityRow;
}

function threadPayload(activities: RecordActivityRow[]): unknown {
  return {
    record_thread: { error: null, error_code: null, activity_count: activities.length, activities },
  };
}

const context: ChatterViewContext = {
  pathname: "/notes/note/nte_1",
  params: { id: "nte_1" },
  route: { name: "notes.note.record", path: "/notes/note/$id", viewType: "notes/note", modelLabel: "notes/note" },
  view: { kind: "record", type: "notes/note", sqid: "nte_1" },
};

beforeEach(() => {
  mocks.mutateCalls = [];
  mocks.useAuthoredQuery.mockReset();
  mocks.useAuthoredQuery.mockImplementation((document: unknown) => {
    const op = operationName(document);
    if (op === "MessagingRecordActivityThread") {
      return { data: mocks.threadData, fetching: false, error: null, refetch: vi.fn() };
    }
    throw new Error(`Unexpected authored query: ${op}`);
  });
});

afterEach(cleanup);

describe("RecordActivityPane", () => {
  test("renders scheduled activities and the scheduler", () => {
    mocks.threadData = threadPayload([activity()]);

    render(<RecordActivityPane context={context} />);

    expect(screen.getByText("Follow up call")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Schedule" })).toBeTruthy();
    // The date picker composes DatePopover with an accessible name.
    expect(screen.getByRole("button", { name: "Due date" })).toBeTruthy();
  });

  test("gates complete/cancel affordances on open activities only", () => {
    mocks.threadData = threadPayload([
      activity({ id: "act_open", summary: "Open task", status: "TODO" }),
      activity({ id: "act_done", summary: "Closed task", status: "DONE", completed_at: "2026-07-01T00:00:00Z" }),
    ]);

    render(<RecordActivityPane context={context} />);

    // Only the open (TODO) activity offers complete + cancel.
    expect(screen.getAllByRole("button", { name: "Mark done" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Cancel activity" })).toHaveLength(1);
  });

  test("cancels through the mutation keyed by activity id", () => {
    mocks.threadData = threadPayload([activity({ id: "act_open" })]);

    render(<RecordActivityPane context={context} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel activity" }));

    expect(
      mocks.mutateCalls.some(
        (call) =>
          call.op === "MessagingCancelRecordActivity" && call.vars.activityId === "act_open",
      ),
    ).toBe(true);
  });

  test("shows the not-enabled state for a record without a thread", () => {
    mocks.threadData = { record_thread: { error_code: "BAD_RECORD" } };

    render(<RecordActivityPane context={context} />);

    expect(screen.getByText("Activities are not enabled")).toBeTruthy();
  });
});

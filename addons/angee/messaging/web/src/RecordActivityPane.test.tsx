// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ToastProvider } from "@angee/ui";
import type { ChatterViewContext } from "@angee/ui/runtime";
import type { RecordActivityRow } from "./documents";

const mocks = vi.hoisted(() => ({
  threadData: undefined as unknown,
  scheduleError: null as Error | null,
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
      if (op === "MessagingScheduleRecordActivity" && mocks.scheduleError) throw mocks.scheduleError;
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

// The pane composes `useActionForm`, whose shared toast owner needs a provider —
// the app shell supplies one in production; the test wraps it the same way.
function renderPane(ctx: ChatterViewContext = context): void {
  render(
    <ToastProvider>
      <RecordActivityPane context={ctx} />
    </ToastProvider>,
  );
}

beforeEach(() => {
  mocks.mutateCalls = [];
  mocks.scheduleError = null;
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

    renderPane();

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

    renderPane();

    // Only the open (TODO) activity offers complete + cancel.
    expect(screen.getAllByRole("button", { name: "Mark done" })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "Cancel activity" })).toHaveLength(1);
  });

  test("cancels through the mutation keyed by activity id", () => {
    mocks.threadData = threadPayload([activity({ id: "act_open" })]);

    renderPane();
    fireEvent.click(screen.getByRole("button", { name: "Cancel activity" }));

    expect(
      mocks.mutateCalls.some(
        (call) =>
          call.op === "MessagingCancelRecordActivity" && call.vars.activityId === "act_open",
      ),
    ).toBe(true);
  });

  test("schedules an activity through the mutation and clears the form", async () => {
    mocks.threadData = threadPayload([]);

    renderPane();
    const summaryInput = screen.getByPlaceholderText("Activity summary");
    fireEvent.change(summaryInput, { target: { value: "  Review the note  " } });
    const noteInput = screen.getByRole("textbox", { name: "Notes" });
    fireEvent.change(noteInput, { target: { value: "Add detail" } });
    fireEvent.click(screen.getByRole("button", { name: "Schedule" }));

    // The migrated form fires the authored schedule mutation with the collected
    // summary through the shared `useActionForm` lifecycle.
    await waitFor(() =>
      expect(
        mocks.mutateCalls.some(
          (call) =>
            call.op === "MessagingScheduleRecordActivity" &&
            call.vars.summary === "Review the note" &&
            call.vars.activityType === "todo" &&
            call.vars.note === "Add detail",
        ),
      ).toBe(true),
    );
    // On success `onSuccess` clears the form fields.
    await waitFor(() =>
      expect((summaryInput as HTMLInputElement).value).toBe(""),
    );
    expect((noteInput as HTMLTextAreaElement).value).toBe("");
  });

  test("requires a non-blank summary before scheduling and describes the error", async () => {
    mocks.threadData = threadPayload([]);
    renderPane();
    const input = screen.getByPlaceholderText("Activity summary");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Schedule" }));

    const error = await screen.findByText("This field is required.");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toContain(error.id);
    expect(mocks.mutateCalls).toEqual([]);
    fireEvent.change(input, { target: { value: "  Review the note  " } });
    fireEvent.click(screen.getByRole("button", { name: "Schedule" }));
    await waitFor(() => expect(mocks.mutateCalls).toEqual([
      expect.objectContaining({ op: "MessagingScheduleRecordActivity", vars: expect.objectContaining({ summary: "Review the note" }) }),
    ]));
  });

  test("retains native form values on failure and allows retry", async () => {
    mocks.threadData = threadPayload([]);
    mocks.scheduleError = new Error("Try again");
    renderPane();
    const input = screen.getByPlaceholderText("Activity summary");
    fireEvent.change(input, { target: { value: "Review the note" } });
    fireEvent.click(screen.getByRole("button", { name: "Schedule" }));
    await screen.findByText("Try again");
    expect((input as HTMLInputElement).value).toBe("Review the note");
    mocks.scheduleError = null;
    fireEvent.click(screen.getByRole("button", { name: "Schedule" }));
    await waitFor(() => expect((input as HTMLInputElement).value).toBe(""));
    expect(screen.queryByText("Try again")).toBeNull();
  });

  test("shows the not-enabled state for a record without a thread", () => {
    mocks.threadData = { record_thread: { error_code: "BAD_RECORD" } };

    renderPane();

    expect(screen.getByText("Activities are not enabled")).toBeTruthy();
  });
});

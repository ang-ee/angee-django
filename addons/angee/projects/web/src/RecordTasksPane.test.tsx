// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { ToastProvider } from "@angee/ui";
import type { ChatterViewContext } from "@angee/ui/runtime";

const mocks = vi.hoisted(() => ({
  data: undefined as unknown,
  calls: [] as Array<{ op: string; vars: Record<string, unknown> }>,
  useAuthoredQuery: vi.fn(),
}));

function operationName(document: unknown): string {
  const definitions = (document as { definitions?: Array<{ name?: { value?: string } }> }).definitions;
  return definitions?.[0]?.name?.value ?? "";
}

/** The action field each pane operation answers through. */
const RESULT_FIELDS: Record<string, string> = {
  ProjectsScheduleRecordTask: "schedule_record_task",
  ProjectsLogRecordTask: "log_record_task",
};

vi.mock("@angee/ui", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@angee/ui")>();
  return {
    ...actual,
    useActionResultRun: () => async (fire: () => Promise<unknown>) => fire(),
    // The shared task verbs: (id, arguments) in, the action outcome out.
    useActionOutcomeMutation: (field: string) => [
      vi.fn(async (id: string, args: Record<string, unknown> = {}) => {
        mocks.calls.push({ op: field, vars: { id, ...args } });
        return { ok: true, message: "Done." };
      }),
      { fetching: false, error: null },
    ],
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
      mocks.calls.push({ op, vars });
      return { [RESULT_FIELDS[op] ?? op]: { ok: true, message: "Done.", validation_errors: null, id: "tsk_1" } };
    });
    return [mutate, { fetching: false, error: null, reset: vi.fn() }];
  },
}));

import { RecordTasksPane } from "./RecordTasksPane";

function context(sqid = "inv_1"): ChatterViewContext {
  return { route: { modelLabel: "billing.Invoice" }, view: { kind: "record", sqid } } as unknown as ChatterViewContext;
}

function task(overrides: Record<string, unknown> = {}) {
  return {
    id: "tsk_1", title: "Chase the credit note", note: "", status: "OPEN", due_date: "2026-03-05", done_at: null,
    activity_type: "call", assignee: { id: "usr_1", username: "ada", display_name: "Ada" },
    task_actions: ["complete", "drop", "reopen"], ...overrides,
  };
}

function payload(tasks: unknown[]) {
  return {
    record_tasks: tasks,
    activity_types: [
      { id: "act_1", key: "todo", name: "To do", glyph: "circle-check" },
      { id: "act_2", key: "call", name: "Call", glyph: "phone" },
    ],
  };
}

function renderPane() {
  return render(<ToastProvider><RecordTasksPane context={context()} /></ToastProvider>);
}

beforeEach(() => {
  mocks.data = payload([]);
  mocks.calls = [];
  mocks.useAuthoredQuery.mockImplementation(() => ({ data: mocks.data, isFetching: false, error: null }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RecordTasksPane", () => {
  test("lists planned and done tasks apart with their type, assignee and day", () => {
    mocks.data = payload([
      task({ id: "tsk_done", title: "Sent the reminder", status: "DONE", activity_type: "todo" }),
      task(),
    ]);
    renderPane();
    const planned = screen.getByRole("region", { name: "Planned" });
    expect(planned.textContent).toContain("Chase the credit note");
    expect(planned.textContent).toContain("Call · Ada");
    expect(screen.getByRole("region", { name: "Done" }).textContent).toContain("Sent the reminder");
  });

  test("schedules the catalog's default kind through the record's mutation and clears the form", async () => {
    renderPane();
    fireEvent.change(screen.getByPlaceholderText("What needs doing"), { target: { value: "  Confirm the IBAN  " } });
    fireEvent.submit(screen.getByPlaceholderText("What needs doing").closest("form")!);
    await waitFor(() => expect(mocks.calls).toHaveLength(1));
    expect(mocks.calls[0]).toEqual({
      op: "ProjectsScheduleRecordTask",
      vars: {
        modelLabel: "billing.Invoice", recordId: "inv_1", title: "Confirm the IBAN", note: "", dueDate: null,
        activityType: null,
      },
    });
    await waitFor(() => expect((screen.getByPlaceholderText("What needs doing") as HTMLInputElement).value).toBe(""));
  });

  test("completes a planned task through the generic verb with its completion note", async () => {
    mocks.data = payload([task()]);
    renderPane();
    fireEvent.change(screen.getByLabelText("Completion note"), { target: { value: "Supplier confirmed." } });
    fireEvent.click(screen.getByRole("button", { name: "Mark done" }));
    await waitFor(() => expect(mocks.calls).toEqual([
      { op: "complete_task", vars: { id: "tsk_1", feedback: "Supplier confirmed." } },
    ]));
  });

  test("cancels a planned task by dropping it", async () => {
    mocks.data = payload([task()]);
    renderPane();
    fireEvent.click(screen.getByRole("button", { name: "Cancel task" }));
    await waitFor(() => expect(mocks.calls).toEqual([{ op: "drop_task", vars: { id: "tsk_1", reason: "OBSOLETE" } }]));
  });

  test("offers no hand verbs where the task's own verbs admit none", () => {
    mocks.data = payload([task({ task_actions: [] })]);
    renderPane();
    expect(screen.getByRole("region", { name: "Planned" }).textContent).toContain("Chase the credit note");
    expect(screen.queryByRole("button", { name: "Mark done" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Cancel task" })).toBeNull();
  });

  test("a log needs the day the exchange happened", async () => {
    renderPane();
    fireEvent.click(screen.getAllByRole("button", { name: "Log" })[0]!);
    fireEvent.change(screen.getByPlaceholderText("What happened"), { target: { value: "Called the supplier" } });
    fireEvent.submit(screen.getByPlaceholderText("What happened").closest("form")!);
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(mocks.calls).toEqual([]);
  });

  test("shows the empty state for a record with nothing planned", () => {
    renderPane();
    expect(screen.getByText("Nothing planned")).toBeTruthy();
  });

  test("asks for a record outside a record view", () => {
    render(<ToastProvider><RecordTasksPane context={{ route: { modelLabel: "billing.Invoice" }, view: { kind: "list" } } as unknown as ChatterViewContext} /></ToastProvider>);
    expect(screen.getByText("No record selected")).toBeTruthy();
  });
});

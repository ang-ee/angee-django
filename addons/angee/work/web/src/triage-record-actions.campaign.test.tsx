// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ButtonHTMLAttributes } from "react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { WorkTaskRow } from "./task-work";

const state = vi.hoisted(() => ({
  record: null as WorkTaskRow | null,
  recordId: "tsk_toolbar",
  formReadOnly: false,
  queue: { triage_enabled: true } as { triage_enabled: boolean } | null,
  task: null as { stage: { category?: string | null; rule_owned?: boolean | null } | null } | null,
  start: vi.fn(),
  returnToTriage: vi.fn(),
  startPending: false,
  returnPending: false,
}));

vi.mock("@angee/projects", () => ({ TASK_MODEL: "projects.Task" }));
vi.mock("@angee/refine", () => ({ extractActionOutcome: vi.fn() }));
vi.mock("./documents", () => ({ AcceptTaskDocument: {} }));
vi.mock("./context", () => ({
  useQueueContext: () => ({ data: { work_queues_by_pk: state.queue } }),
  useTaskContext: () => ({ data: state.task ? { project_tasks_by_pk: state.task } : null }),
}));
vi.mock("./i18n", () => ({ useWorkT: () => (key: string) => key }));
vi.mock("@angee/ui", () => ({
  // The shared trigger/hook have their own dirty/error lifecycle tests. Here
  // their public props are the seam for the addon's branch and pending tests.
  RecordActionTrigger: ({ loading, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { loading?: boolean }) => (
    <button {...props} disabled={props.disabled || loading} aria-busy={loading} />
  ),
  ActionFormDialog: () => null,
  Group: () => null,
  Field: () => null,
  RelativeTime: () => null,
  canonicalOptionValue: vi.fn(),
  defineRowAction: (value: unknown) => value,
  relationValueId: (value: unknown) => typeof value === "object" && value !== null && "id" in value ? value.id : "",
  useActionOutcomeMutation: () => [vi.fn()],
  useAuthoredResourceMutation: () => [vi.fn()],
  useRecordChromeContext: () => state,
  useRecordChromeActionMutation: (action: string) => action === "start_task"
    ? [state.start, { fetching: state.startPending }]
    : [state.returnToTriage, { fetching: state.returnPending }],
}));

import { TriageRecordActions } from "./triage-actions";

beforeEach(() => {
  state.record = { id: "tsk_toolbar", queue: { id: "que_work" }, stage: { category: "UNSTARTED" } };
  state.task = null;
  state.formReadOnly = false;
  state.queue = { triage_enabled: true };
  state.startPending = state.returnPending = false;
  state.start.mockReset();
  state.returnToTriage.mockReset();
});
afterEach(cleanup);

describe("task record Start and Return to triage", () => {
  test.each(["BACKLOG", "UNSTARTED", "backlog", "unstarted"])("offers Start for %s and passes the record id", (category) => {
    state.task = { stage: { category } };
    render(<TriageRecordActions />);
    fireEvent.click(screen.getByRole("button", { name: "task.action.start" }));
    fireEvent.click(screen.getByRole("button", { name: "triage.action.return" }));
    expect(state.start).toHaveBeenCalledExactlyOnceWith("tsk_toolbar");
    expect(state.returnToTriage).toHaveBeenCalledExactlyOnceWith("tsk_toolbar");
  });

  test.each(["STARTED", "COMPLETED", "CANCELED", "DUPLICATE"])("does not offer Start for %s", (category) => {
    state.task = { stage: { category } };
    render(<TriageRecordActions />);
    expect(screen.queryByRole("button", { name: "task.action.start" })).toBeNull();
    expect(screen.getByRole("button", { name: "triage.action.return" })).toBeTruthy();
  });

  test.each(["absent record", "absent queue", "read only"])("renders no actions with %s", (condition) => {
    if (condition === "absent record") state.record = null;
    else if (condition === "absent queue") state.record = { id: "tsk_toolbar", queue: null };
    else state.formReadOnly = true;
    render(<TriageRecordActions />);
    expect(screen.queryAllByRole("button")).toEqual([]);
  });

  test.each([false, null])("requires an enabled queue for Return (%s)", (enabled) => {
    state.queue = enabled === null ? null : { triage_enabled: enabled };
    render(<TriageRecordActions />);
    expect(screen.queryByRole("button", { name: "triage.action.return" })).toBeNull();
    expect(screen.getByRole("button", { name: "task.action.start" })).toBeTruthy();
  });

  test.each(["BACKLOG", "UNSTARTED", "STARTED", "COMPLETED"])("rule-owned %s hides both hand actions", (category) => {
    state.task = { stage: { category, rule_owned: true } };
    render(<TriageRecordActions />);
    expect(screen.queryAllByRole("button")).toEqual([]);
  });

  test.each(["loading", "redacted"])("does not offer Start when category is %s", (condition) => {
    state.record = { id: "tsk_toolbar", queue: { id: "que_work" }, stage: null };
    state.task = condition === "redacted" ? { stage: null } : null;
    render(<TriageRecordActions />);
    expect(screen.queryByRole("button", { name: "task.action.start" })).toBeNull();
  });

  test("authored category replaces a stale form category after refresh", () => {
    const view = render(<TriageRecordActions />);
    expect(screen.getByRole("button", { name: "task.action.start" })).toBeTruthy();
    state.task = { stage: { category: "STARTED" } };
    view.rerender(<TriageRecordActions />);
    expect(screen.queryByRole("button", { name: "task.action.start" })).toBeNull();
  });

  test.each(["start", "return"])("coordinates shared triggers while %s is pending", (pending) => {
    state.startPending = pending === "start";
    state.returnPending = pending === "return";
    render(<TriageRecordActions />);
    const start = screen.getByRole("button", { name: "task.action.start" });
    const back = screen.getByRole("button", { name: "triage.action.return" });
    fireEvent.click(start);
    fireEvent.click(back);
    expect(state.start).not.toHaveBeenCalled();
    expect(state.returnToTriage).not.toHaveBeenCalled();
    expect(start.getAttribute("aria-busy")).toBe(String(state.startPending));
    expect(back.getAttribute("aria-busy")).toBe(String(state.returnPending));
  });

  test("keeps the four existing triage actions", () => {
    state.record = { id: "tsk_toolbar", queue: { id: "que_work" }, started_triage_at: "2026-01-01", triaged_at: null };
    state.task = { stage: { category: "TRIAGE" } };
    render(<TriageRecordActions />);
    expect(screen.getAllByRole("button").map((button) => button.textContent)).toEqual([
      "triage.action.accept", "triage.action.decline", "triage.action.snooze", "triage.action.duplicate",
    ]);
  });
});

// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  rows: [] as Record<string, unknown>[],
  queryVariables: null as unknown,
  restore: vi.fn(),
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredQuery: (_document: unknown, variables: unknown) => {
    mocks.queryVariables = variables;
    return { data: { removed_tasks: mocks.rows }, error: null };
  },
}));

vi.mock("@angee/ui", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/ui")>()),
  useActionResultRun: () => async (fire: () => Promise<unknown>) => await fire(),
  useAuthoredResourceMutation: () => [mocks.restore, { fetching: false, error: null }],
}));

vi.mock("@angee/projects", () => ({ TASK_MODEL: "projects.Task" }));

import { RemovedTasks } from "./removed-tasks";

beforeEach(() => {
  mocks.restore.mockReset();
  mocks.restore.mockResolvedValue({ restore_task: { ok: true, message: "Task restored." } });
});

afterEach(cleanup);

describe("RemovedTasks", () => {
  test("lists a parent's removed questions and restores one at its revision", async () => {
    mocks.rows = [{
      id: "tsk_q", title: "Can this ship today?", revision: 4, parent: "tsk_req",
      removed_at: "2026-10-01T00:00:00Z", removed_by_label: "Ada", removal_reason: "Answered elsewhere",
    }];
    render(<RemovedTasks parent="tsk_req" />);

    expect(mocks.queryVariables).toEqual({ queue: null, parent: "tsk_req" });
    fireEvent.click(screen.getByRole("button", { name: "Removed (1)" }));
    expect(screen.getByText("Can this ship today?")).toBeTruthy();
    expect(screen.getByText("Reason: Answered elsewhere")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(mocks.restore).toHaveBeenCalledWith({ task: "tsk_q", expected_revision: 4 }));
  });

  test("renders nothing for a reader the server lists no removed tasks for", () => {
    mocks.rows = [];
    const { container } = render(<RemovedTasks queue="que_eng" />);
    expect(container.textContent).toBe("");
  });
});

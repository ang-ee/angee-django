// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

const state = vi.hoisted(() => ({ error: null as Error | null, noRun: false, refetch: vi.fn() }));
vi.mock("@angee/refine", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/refine")>(),
  useAuthoredQuery: (_document: unknown, variables: { session: string }) => ({
    data: state.error ? undefined : { agent_session_workflow_run: state.noRun ? null : `run:${variables.session}` },
    isFetching: false,
    error: state.error,
    refetch: state.refetch,
  }),
}));
vi.mock("@angee/workflows", () => ({
  useWorkflowsT: () => (key: string) => key,
  WorkflowApprovals: ({ runId }: { runId: string }) => <label>approval:{runId}<input aria-label="decision value" /></label>,
}));

import { SessionApprovals } from "./SessionApprovals";

afterEach(() => { cleanup(); state.error = null; state.noRun = false; state.refetch.mockReset(); });

test("session approvals stay bounded and retain mounted task state across transcript refreshes", () => {
  const view = render(<SessionApprovals sessionId="A" />);
  const input = screen.getByLabelText("decision value") as HTMLInputElement;
  fireEvent.change(input, { target: { value: "kept" } });
  view.rerender(<SessionApprovals sessionId="A" />);

  expect(screen.getByLabelText("decision value")).toBe(input);
  expect(input.value).toBe("kept");
  expect(screen.getByText("approval:run:A").closest("[class*='max-h']")).toBeTruthy();
});

test("query failure offers Retry while an authoritative missing run is an empty state", () => {
  state.error = new Error("Session lookup failed");
  const view = render(<SessionApprovals sessionId="A" />);
  fireEvent.click(screen.getByRole("button", { name: "input.retry" }));
  expect(state.refetch).toHaveBeenCalledTimes(1);

  state.error = null;
  state.noRun = true;
  view.rerender(<SessionApprovals sessionId="A" />);
  expect(screen.getByText("inbox.sourceUnavailable")).toBeTruthy();
});

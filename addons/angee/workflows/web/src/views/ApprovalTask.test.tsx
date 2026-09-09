// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";
import { createRouteHref } from "@angee/ui/runtime";

const mocks = vi.hoisted(() => ({
  decide: vi.fn(async (): Promise<unknown> => ({
    decide: {
      decision: {
        id: "decision-1", verdict: "COMPLETED", resolution: {},
        updated_at: "2026-09-08T08:01:00Z",
      },
      validation_errors: null,
    },
  })),
  mutationState: { fetching: false, error: null as Error | null },
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@angee/refine")>()),
  useAuthoredMutation: () => [mocks.decide, mocks.mutationState],
}));

import type { PendingWorkflowDecision } from "../documents.public";
import { ApprovalTask } from "./ApprovalTask";

const approval = {
  id: "decision-1",
  action: "review",
  priority: 10,
  payload: { subject: "A note" },
  verdict: "PENDING",
  attempts: 0,
  max_attempts: 3,
  expires_at: null,
  escalate_at: null,
  decision_schema: null,
  workflow_name: "Publish note",
  step_name: "Review publication",
  created_at: "2026-09-08T08:00:00Z",
  updated_at: "2026-09-08T08:00:00Z",
} as PendingWorkflowDecision;

afterEach(() => {
  cleanup();
  mocks.decide.mockClear();
  mocks.decide.mockResolvedValue({
    decide: {
      decision: {
        id: "decision-1", verdict: "COMPLETED", resolution: {},
        updated_at: "2026-09-08T08:01:00Z",
      },
      validation_errors: null,
    },
  });
  mocks.mutationState.fetching = false;
  mocks.mutationState.error = null;
});

describe("ApprovalTask", () => {
  test("puts the resolution before closed source data and preserves resolution mutation variables", async () => {
    const onResolved = vi.fn();
    render(<ApprovalTask approval={approval} onResolved={onResolved} />);

    const resolution = screen.getByLabelText("Resolution payload");
    const sourceTrigger = screen.getByRole("button", { name: "Source data" });
    expect(resolution.compareDocumentPosition(sourceTrigger) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(sourceTrigger.getAttribute("aria-expanded")).toBe("false");

    fireEvent.change(resolution, { target: { value: '{"approved":true}' } });
    fireEvent.click(screen.getByRole("button", { name: /Complete/ }));

    await waitFor(() => expect(mocks.decide).toHaveBeenCalledWith({
      decision: "decision-1",
      verdict: "COMPLETE",
      payload: { approved: true },
    }));
    expect(onResolved).toHaveBeenCalledOnce();
  });

  test("renders the narrow-context back action supplied by its host", () => {
    const onBack = vi.fn();
    render(<ApprovalTask approval={approval} onBack={onBack} onResolved={() => undefined} />);
    fireEvent.click(screen.getByRole("button", { name: /Back to approvals/ }));
    expect(onBack).toHaveBeenCalledOnce();
  });

  test("links independently authorized run, execution, and attempt context", () => {
    render(<AppRuntimeProvider runtime={{
      widgets: defaultWidgets,
      routeHref: createRouteHref([{ name: "workflows.run", path: "/runs/$id" }]),
    }}><ApprovalTask approval={{
      ...approval,
      source_run_id: "run-1",
      source_execution_id: "execution-2",
      source_attempt_id: "attempt-3",
    }} onResolved={() => undefined} /></AppRuntimeProvider>);

    expect(screen.getByRole("link", { name: "Open source run" }).getAttribute("href")).toBe("/runs/run-1");
    expect(screen.getByRole("link", { name: "Execution execution-2" }).getAttribute("href")).toContain("execution=execution-2");
    expect(screen.getByRole("link", { name: "Attempt attempt-3" }).getAttribute("href")).toContain("attempt=attempt-3");
  });

  test("keeps edited structured values when the server returns a field error", async () => {
    mocks.decide.mockResolvedValueOnce({
      decide: { validation_errors: { title: ["Choose another title."] } },
    });
    render(
      <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
        <ApprovalTask
          approval={{
            ...approval,
            payload: { title: "Original" },
            decision_schema: {
              type: "object",
              required: ["title"],
              properties: {
                title: { type: "string", label: "Title" },
              },
            },
          }}
          onResolved={() => undefined}
        />
      </AppRuntimeProvider>,
    );

    const title = screen.getByLabelText("Title");
    fireEvent.change(title, { target: { value: "Edited" } });
    fireEvent.click(screen.getByRole("button", { name: /Complete/ }));

    expect(await screen.findByText("Choose another title.")).toBeTruthy();
    expect((title as HTMLInputElement).value).toBe("Edited");
    expect(mocks.decide).toHaveBeenCalledWith({
      decision: "decision-1",
      verdict: "COMPLETE",
      payload: { title: "Edited" },
    });
  });

  test.each([
    ["a missing mutation payload", {}],
    ["a different decision", {
      decide: {
        decision: {
          id: "decision-2", verdict: "COMPLETED", resolution: {},
          updated_at: "2026-09-08T08:01:00Z",
        },
        validation_errors: null,
      },
    }],
    ["a nonterminal verdict", {
      decide: {
        decision: {
          id: "decision-1", verdict: "PENDING", resolution: {},
          updated_at: "2026-09-08T08:01:00Z",
        },
        validation_errors: null,
      },
    }],
  ])("does not resolve or discard edited values for %s", async (_label, response) => {
    mocks.decide.mockResolvedValueOnce(response);
    const onResolved = vi.fn();
    render(<ApprovalTask approval={approval} onResolved={onResolved} />);

    const resolution = screen.getByLabelText("Resolution payload");
    fireEvent.change(resolution, { target: { value: '{"approved":true}' } });
    fireEvent.click(screen.getByRole("button", { name: /Complete/ }));

    expect(await screen.findByText("The approval response could not confirm this decision.")).toBeTruthy();
    expect((resolution as HTMLTextAreaElement).value).toBe('{"approved":true}');
    expect(onResolved).not.toHaveBeenCalled();
  });

  test("reconciles an ambiguous mutation before retrying the exact decision", async () => {
    mocks.decide
      .mockRejectedValueOnce(new Error("Connection lost"))
      .mockResolvedValueOnce({
        decide: {
          decision: {
            id: "decision-1", verdict: "COMPLETED", resolution: {},
            updated_at: "2026-09-08T08:01:00Z",
          },
          validation_errors: null,
        },
      });
    const reconcile = vi.fn(async () => approval);
    const onResolved = vi.fn();
    render(<ApprovalTask approval={approval} onResolved={onResolved} reconcile={reconcile} />);
    const resolution = screen.getByLabelText("Resolution payload");
    fireEvent.change(resolution, { target: { value: '{"approved":true}' } });

    fireEvent.click(screen.getByRole("button", { name: /Complete/ }));
    expect(await screen.findByText("Connection lost")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Complete/ }));

    await waitFor(() => expect(onResolved).toHaveBeenCalledOnce());
    expect(reconcile).toHaveBeenCalledWith("decision-1");
    expect(mocks.decide).toHaveBeenCalledTimes(2);
    expect(mocks.decide).toHaveBeenLastCalledWith({
      decision: "decision-1", verdict: "COMPLETE", payload: { approved: true },
    });
  });

  test("keeps an ambiguous approval open when reconciliation cannot read it", async () => {
    mocks.decide.mockRejectedValueOnce(new Error("Connection lost"));
    const reconcile = vi.fn(async () => null);
    const onResolved = vi.fn();
    render(<ApprovalTask approval={approval} onResolved={onResolved} reconcile={reconcile} />);
    const resolution = screen.getByLabelText("Resolution payload");
    fireEvent.change(resolution, { target: { value: '{"approved":true}' } });

    fireEvent.click(screen.getByRole("button", { name: /Complete/ }));
    expect(await screen.findByText("Connection lost")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Complete/ }));

    expect(await screen.findByText("This approval is unavailable or you no longer have access.")).toBeTruthy();
    expect((resolution as HTMLTextAreaElement).value).toBe('{"approved":true}');
    expect(mocks.decide).toHaveBeenCalledOnce();
    expect(onResolved).not.toHaveBeenCalled();
  });

  test("reconciles invalid validation metadata and admits only one in-flight action", async () => {
    let settle: ((value: unknown) => void) | undefined;
    mocks.decide.mockImplementationOnce(() => new Promise((resolve) => { settle = resolve; }));
    const reconcile = vi.fn(async () => approval);
    render(<ApprovalTask approval={approval} onResolved={() => undefined} reconcile={reconcile} />);

    fireEvent.click(screen.getByRole("button", { name: /Complete/ }));
    fireEvent.click(screen.getByRole("button", { name: /Reject/ }));
    expect(mocks.decide).toHaveBeenCalledOnce();
    await waitFor(() => expect((screen.getByRole("button", { name: /Complete/ }) as HTMLButtonElement).disabled).toBe(true));
    settle?.({ decide: { decision: null, validation_errors: ["invalid"] } });
    expect(await screen.findByText("Approval validation errors have an invalid shape.")).toBeTruthy();

    mocks.decide.mockResolvedValueOnce({
      decide: {
        decision: {
          id: "decision-1", verdict: "COMPLETED", resolution: {},
          updated_at: "2026-09-08T08:01:00Z",
        },
        validation_errors: null,
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /Complete/ }));
    await waitFor(() => expect(reconcile).toHaveBeenCalledWith("decision-1"));
    expect(mocks.decide).toHaveBeenCalledTimes(2);
  });

  test("preserves values for refreshes of one decision and resets them for another", () => {
    const { rerender } = render(<ApprovalTask approval={approval} onResolved={() => undefined} />);
    const resolution = screen.getByLabelText("Resolution payload");
    fireEvent.change(resolution, { target: { value: '{"decision":"A"}' } });

    rerender(<ApprovalTask approval={{ ...approval, updated_at: "2026-09-08T08:02:00Z" }} onResolved={() => undefined} />);
    expect((screen.getByLabelText("Resolution payload") as HTMLTextAreaElement).value).toBe('{"decision":"A"}');

    rerender(<ApprovalTask approval={{ ...approval, id: "decision-2" }} onResolved={() => undefined} />);
    expect((screen.getByLabelText("Resolution payload") as HTMLTextAreaElement).value).toBe("{}");
  });

  test("keeps entered values read-only when refreshed to a terminal decision", () => {
    const { rerender } = render(<ApprovalTask approval={approval} onResolved={() => undefined} />);
    fireEvent.change(screen.getByLabelText("Resolution payload"), { target: { value: '{"note":"mine"}' } });

    rerender(<ApprovalTask approval={{ ...approval, verdict: "EXPIRED", resolution: { confirmed: true } }} onResolved={() => undefined} />);

    expect(screen.getByText("This approval is no longer pending.")).toBeTruthy();
    expect((screen.getByLabelText("Resolution payload") as HTMLTextAreaElement).value).toBe('{\n  "confirmed": true\n}');
    expect((screen.getByLabelText("Resolution payload") as HTMLTextAreaElement).readOnly).toBe(true);
    expect(screen.queryByRole("button", { name: /Complete/ })).toBeNull();
  });

  test("keeps pending values read-only when exact reconciliation becomes unavailable", () => {
    const { rerender } = render(<ApprovalTask approval={approval} onResolved={() => undefined} />);
    fireEvent.change(screen.getByLabelText("Resolution payload"), { target: { value: '{"note":"retain"}' } });

    rerender(<ApprovalTask approval={approval} available={false} onResolved={() => undefined} />);

    expect(screen.getByText("This approval is unavailable or you no longer have access.")).toBeTruthy();
    expect((screen.getByLabelText("Resolution payload") as HTMLTextAreaElement).value).toBe('{"note":"retain"}');
    expect((screen.getByLabelText("Resolution payload") as HTMLTextAreaElement).readOnly).toBe(true);
    expect(screen.queryByRole("button", { name: /Complete/ })).toBeNull();
  });

  test("disables every verdict while a resolution is pending", () => {
    mocks.mutationState.fetching = true;
    render(<ApprovalTask approval={approval} onResolved={() => undefined} />);

    for (const name of ["Escalate", "Reject", "Complete"]) {
      expect(
        (screen.getByRole("button", { name: new RegExp(name) }) as HTMLButtonElement)
          .disabled,
      ).toBe(true);
    }
  });
});

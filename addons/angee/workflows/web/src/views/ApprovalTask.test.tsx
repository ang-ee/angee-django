// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, defaultWidgets } from "@angee/ui";

const mocks = vi.hoisted(() => ({
  decide: vi.fn(
    async (): Promise<{ decide: { validation_errors: unknown } }> => ({
      decide: { validation_errors: null },
    }),
  ),
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
  mocks.decide.mockResolvedValue({ decide: { validation_errors: null } });
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

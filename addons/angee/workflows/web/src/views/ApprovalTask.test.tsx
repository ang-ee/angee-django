// @vitest-environment happy-dom

import type { ReactNode } from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { AppRuntimeProvider, createRouteHref, defaultWidgets, type JsonValue } from "@angee/ui";
import { ModelMetadataProvider, schemaFieldMetadataFromDataResources } from "@angee/metadata";
import { testDataResource } from "@angee/metadata/testing";

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

vi.mock("../documents.public", () => ({ DecideWorkflowDecisionDocument: { kind: "Document", name: "DecideWorkflowDecision" } }));

vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, { useConfirm: () => async () => true });
});

import type { PendingWorkflowDecision } from "../documents.public";
import { WORKFLOW_DECISION_CONTENT_SLOT } from "../slots";
import { ApprovalTask, DecisionField, type WorkflowDecisionContentProps } from "./ApprovalTask";

function TestRuntime({ children }: { children: ReactNode }) {
  return <AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>{children}</AppRuntimeProvider>;
}

const approval = {
  id: "decision-1",
  action: "review",
  priority: 10,
  payload: { subject: "A note" },
  verdict: "PENDING",
  resolution: {},
  resolved_by: "",
  attempts: 0,
  max_attempts: 3,
  expires_at: null,
  escalate_at: null,
  decision_schema: null,
  source_run_id: null,
  source_execution_id: null,
  source_attempt_id: null,
  workflow_name: "Publish note",
  step_name: "Review publication",
  created_at: "2026-09-08T08:00:00Z",
  updated_at: "2026-09-08T08:00:00Z",
} satisfies PendingWorkflowDecision;

const titleActionSchema: JsonValue = {
  type: "object", required: ["action"], properties: {
    action: { type: "string", enum: ["record"], options: [
      { value: "record", label: "Record decision", verdict: "COMPLETE" },
    ] },
    title: { type: "string", label: "Title" },
  },
  oneOf: [{ type: "object", required: ["action", "title"], properties: {
    action: { const: "record" }, title: { type: "string" },
  }, additionalProperties: false }],
};
const authoredApproval: PendingWorkflowDecision = {
  ...approval,
  payload: { title: "Original" },
  decision_schema: titleActionSchema,
};

const obsoleteHistoricalSchema: JsonValue = {
  type: "object",
  required: ["action"],
  properties: {
    action: { type: "string", enum: ["record"], options: [
      { value: "record", label: "Record decision", verdict: "COMPLETE" },
    ] },
    title: { type: "string", label: "Title", default: "Default title" },
    obsolete_choice: { type: "string", enum: [] },
    review_context: { type: "object", layout: "context", widget: "object", readOnly: true },
  },
  oneOf: [{
    type: "object",
    required: ["action", "obsolete_choice"],
    properties: {
      action: { const: "record" },
      obsolete_choice: { type: "string", enum: [] },
    },
    additionalProperties: false,
  }],
};

const correctionActionSchema: JsonValue = {
  type: "object", required: ["action"], properties: {
    action: { type: "string", enum: ["correct", "reject"], options: [
      { value: "correct", label: "Correct source facts", verdict: "COMPLETE" },
      { value: "reject", label: "Reject document", verdict: "REJECT" },
    ] },
    note: { type: "string", label: "Review explanation", minLength: 1 },
    currency: { type: ["string", "null"], label: "Invoice currency", omittable: true },
    invoice_date: { type: ["string", "null"], label: "Invoice date", widget: "date", omittable: true },
    vendor_name: { type: ["string", "null"], label: "Supplier name", omittable: true },
  },
  oneOf: [
    { type: "object", required: ["action", "note"], properties: {
      action: { const: "correct" }, note: { type: "string", minLength: 1 },
      currency: { type: ["string", "null"] }, invoice_date: { type: ["string", "null"] },
      vendor_name: { type: ["string", "null"] },
    }, anyOf: [
      { required: ["currency"], properties: { currency: { type: "string", minLength: 1 } } },
      { required: ["invoice_date"], properties: { invoice_date: { type: "string", minLength: 1 } } },
      { required: ["vendor_name"], properties: { vendor_name: { type: "string", minLength: 1 } } },
    ], additionalProperties: false },
    { type: "object", required: ["action", "note"], properties: {
      action: { const: "reject" }, note: { type: "string", minLength: 1 },
    }, additionalProperties: false },
  ],
};

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
  test("fails closed before collapsed processing details when no action schema was authored", () => {
    render(<ModelMetadataProvider metadata={schemaFieldMetadataFromDataResources([
      testDataResource("notes.Note"),
    ])}><AppRuntimeProvider runtime={{
      widgets: defaultWidgets,
      routesByResource: { "notes.Note": {
        collection: "notes", record: { name: "notes.note", param: "id" },
      } },
      routeHref: createRouteHref([{ name: "notes.note", path: "/notes/$id" }]),
    }}><ApprovalTask approval={{ ...approval, target_reference: {
      model: "notes.Note", id: "note-1", tab: null, label: "A note",
    } }} onResolved={() => undefined} /></AppRuntimeProvider></ModelMetadataProvider>);

    const unavailable = screen.getByText(/This approval has no authored action form/);
    const target = screen.getByRole("link", { name: "Open related record" });
    const sourceTrigger = screen.getByRole("button", { name: "Processing details" });
    expect(target.getAttribute("href")).toContain("/notes/note-1");
    expect(unavailable.compareDocumentPosition(target) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(unavailable.compareDocumentPosition(sourceTrigger) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(sourceTrigger.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: /Complete/ })).toBeNull();
    expect(mocks.decide).not.toHaveBeenCalled();
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

    fireEvent.click(screen.getByRole("button", { name: "Processing details" }));
    expect(screen.getByRole("link", { name: "Open source run" }).getAttribute("href")).toBe("/runs/run-1");
    expect(screen.getByRole("link", { name: "Execution execution-2" }).getAttribute("href")).toContain("execution=execution-2");
    expect(screen.getByRole("link", { name: "Attempt attempt-3" }).getAttribute("href")).toContain("attempt=attempt-3");
  });

  test("records one decision and retries only the queue refresh after continuation failure", async () => {
    const onResolved = vi.fn()
      .mockRejectedValueOnce(new Error("queue unavailable"))
      .mockResolvedValueOnce(undefined);
    function Specialized({ readOnly }: WorkflowDecisionContentProps) {
      return <span>{readOnly ? "Decision is read only" : "Frozen review details"}</span>;
    }
    render(<AppRuntimeProvider runtime={{
      widgets: defaultWidgets,
      slots: [{
        slot: WORKFLOW_DECISION_CONTENT_SLOT,
        model: "workflows.Decision",
        impl: "review",
        id: "test.post-commit-refresh",
        content: Specialized,
      }],
    }}><ApprovalTask approval={{ ...approval, payload: { title: "Review" }, decision_schema: titleActionSchema }} onResolved={onResolved} /></AppRuntimeProvider>);

    fireEvent.click((await screen.findAllByRole("button", { name: "Record decision" }))[0]!);
    fireEvent.click((await screen.findAllByRole("button", { name: "Record decision" }))[1]!);

    expect(await screen.findByText("Decision recorded")).toBeTruthy();
    expect(screen.getByText("The decision was recorded, but the approval queue could not refresh.")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Record decision" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("button", { name: /Complete/ })).toBeNull();
    expect(mocks.decide).toHaveBeenCalledOnce();
    expect(onResolved).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole("button", { name: "Retry queue refresh" }));
    await waitFor(() => expect(onResolved).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText("Decision recorded")).toBeNull());
    expect(mocks.decide).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: /Complete/ })).toBeNull();
  });

  test("lets a contributed fragment place one native field after the action choice", () => {
    function Specialized(props: WorkflowDecisionContentProps) {
      return <div data-testid="specialized-field"><DecisionField name="title" props={props} /></div>;
    }
    Object.assign(Specialized, { renderedInputFields: ["title"] });
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets, slots: [{
      slot: WORKFLOW_DECISION_CONTENT_SLOT,
      model: "workflows.Decision",
      impl: "review",
      id: "test.owned-decision-field",
      content: Specialized,
    }] }}><ApprovalTask approval={{ ...approval, decision_schema: titleActionSchema }}
      onResolved={() => undefined} /></AppRuntimeProvider>);

    const action = screen.getByRole("button", { name: "Record decision" });
    expect(screen.queryByLabelText("Title")).toBeNull();
    fireEvent.click(action);
    const input = screen.getByLabelText("Title");
    expect(screen.getAllByLabelText("Title")).toHaveLength(1);
    expect(action.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  test("shows the native compile error without invoking contributed content", () => {
    const rendered = vi.fn();
    function Specialized() {
      rendered();
      return <span>Specialized content</span>;
    }
    const invalidSchema = {
      type: "object", required: ["action"], properties: {
        action: { type: "string", options: [{ value: "record", label: "" }] },
      },
    } satisfies JsonValue;
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets, slots: [{
      slot: WORKFLOW_DECISION_CONTENT_SLOT,
      model: "workflows.Decision",
      impl: "review",
      id: "test.invalid-specialized-form",
      content: Specialized,
    }] }}><ApprovalTask approval={{ ...approval, decision_schema: invalidSchema }}
      onResolved={() => undefined} /></AppRuntimeProvider>);

    expect(screen.getByText(/Invalid action\.options\.0\.label/)).toBeTruthy();
    expect(screen.queryByText("Frozen Decision context is unavailable.")).toBeNull();
    expect(rendered).not.toHaveBeenCalled();
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
            decision_schema: titleActionSchema,
          }}
          onResolved={() => undefined}
        />
      </AppRuntimeProvider>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    const title = await screen.findByLabelText("Title");
    fireEvent.change(title, { target: { value: "Edited" } });
    fireEvent.click((await screen.findAllByRole("button", { name: "Record decision" }))[1]!);

    expect(await screen.findByText("Choose another title.")).toBeTruthy();
    expect((title as HTMLInputElement).value).toBe("Edited");
    expect(mocks.decide).toHaveBeenCalledWith({
      decision: "decision-1",
      verdict: "COMPLETE",
      payload: { action: "record", title: "Edited" },
    });
  });

  test("keeps dirty action fields across a same-decision refetch and submits through the form", async () => {
    const { rerender } = render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <ApprovalTask approval={{ ...approval, payload: { title: "Original" }, decision_schema: titleActionSchema }}
        onResolved={() => undefined} />
    </AppRuntimeProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    const title = screen.getByLabelText("Title") as HTMLInputElement;
    fireEvent.change(title, { target: { value: "Edited locally" } });
    rerender(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <ApprovalTask approval={{ ...approval, updated_at: "2026-09-08T08:02:00Z",
        payload: { title: "Fresh server suggestion" }, decision_schema: titleActionSchema }}
        onResolved={() => undefined} />
    </AppRuntimeProvider>);
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Edited locally");
    fireEvent.submit((screen.getByLabelText("Title") as HTMLInputElement).form!);
    await waitFor(() => expect(mocks.decide).toHaveBeenCalledWith({
      decision: "decision-1", verdict: "COMPLETE",
      payload: { action: "record", title: "Edited locally" },
    }));
  });

  test("reconciles a terminal same-decision update to the retained server resolution", async () => {
    const { rerender } = render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <ApprovalTask approval={{ ...approval, payload: { title: "Original" }, decision_schema: titleActionSchema }}
        onResolved={() => undefined} />
    </AppRuntimeProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Unsaved edit" } });
    rerender(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
      <ApprovalTask approval={{ ...approval, verdict: "COMPLETED", updated_at: "2026-09-08T08:03:00Z",
        payload: { title: "Original" }, resolution: { action: "record", title: "Server retained" },
        decision_schema: titleActionSchema }} onResolved={() => undefined} />
    </AppRuntimeProvider>);
    await waitFor(() => expect(screen.getByText("Server retained")).toBeTruthy());
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
  });

  test("shows typed frozen context and submits only the selected native action branch", async () => {
    const schema: JsonValue = {
      type: "object", required: ["action"], properties: {
        action: { type: "string", enum: ["approve", "reject"], options: [
          { value: "approve", label: "Approve source", verdict: "COMPLETE" },
          { value: "reject", label: "Reject source", verdict: "REJECT" },
        ] },
        note: { type: "string", label: "Review note" },
        record: { type: "object", layout: "context", widget: "record" },
      },
      oneOf: [
        { type: "object", required: ["action"], properties: { action: { const: "approve" } }, additionalProperties: false },
        { type: "object", required: ["action", "note"], properties: {
          action: { const: "reject" }, note: { type: "string", minLength: 1 },
        }, additionalProperties: false },
      ],
    };
    const widgets = { ...defaultWidgets, record: { read: ({ value }: { value?: unknown }) =>
      <span>{(value as { label?: string })?.label}</span> } };
    function Specialized({ contextValues }: WorkflowDecisionContentProps) {
      return <span>{(contextValues.record as { label?: string })?.label}</span>;
    }
    render(<AppRuntimeProvider runtime={{ widgets, slots: [{
      slot: WORKFLOW_DECISION_CONTENT_SLOT, model: "workflows.Decision", impl: "review",
      id: "test.context-only-fragment", content: Specialized,
    }] }}><ApprovalTask approval={{ ...approval,
      payload: { record: { model: "storage.File", id: "fil_source", label: "Frozen invoice A" } },
      decision_schema: schema,
    }} onResolved={() => undefined} /></AppRuntimeProvider>);

    expect(await screen.findByText("Frozen invoice A")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reject source" }));
    fireEvent.change(screen.getByLabelText("Review note"), { target: { value: "Wrong source" } });
    fireEvent.click((await screen.findAllByRole("button", { name: "Reject source" }))[1]!);
    await waitFor(() => expect(mocks.decide).toHaveBeenCalledWith({
      decision: "decision-1", verdict: "REJECT", payload: { action: "reject", note: "Wrong source" },
    }));
  });

  test("keeps an explanation-only alternative action pending with one actionable group error", async () => {
    render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}><ApprovalTask approval={{
      ...approval, payload: {}, decision_schema: correctionActionSchema,
    }} onResolved={() => undefined} /></AppRuntimeProvider>);

    fireEvent.click(screen.getByRole("button", { name: "Correct source facts" }));
    expect(screen.getByLabelText("Invoice currency")).toBeTruthy();
    expect(screen.getByLabelText("Invoice date")).toBeTruthy();
    expect(screen.getByLabelText("Supplier name")).toBeTruthy();
    expect(screen.queryByText("Set value")).toBeNull();
    expect(screen.queryByText("Not set")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: "Correct source facts" })[1]!);
    expect(await screen.findByText("Review explanation must contain at least 1 character.")).toBeTruthy();
    expect(screen.getByText("Complete at least one of: Invoice currency, Invoice date, or Supplier name.")).toBeTruthy();
    expect(mocks.decide).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Review explanation"), { target: { value: "Browser form validation only." } });
    fireEvent.click(screen.getAllByRole("button", { name: "Correct source facts" })[1]!);
    expect(screen.queryByText("Review explanation must contain at least 1 character.")).toBeNull();
    expect(screen.getByText("Complete at least one of: Invoice currency, Invoice date, or Supplier name.")).toBeTruthy();
    expect(mocks.decide).not.toHaveBeenCalled();
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
    render(<TestRuntime><ApprovalTask approval={authoredApproval} onResolved={onResolved} /></TestRuntime>);
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    const title = screen.getByLabelText("Title");
    fireEvent.change(title, { target: { value: "Approved" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Record decision" })[1]!);

    expect(await screen.findByText("The approval response could not confirm this decision.")).toBeTruthy();
    expect((title as HTMLInputElement).value).toBe("Approved");
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
    const reconcile = vi.fn(async () => authoredApproval);
    const onResolved = vi.fn();
    render(<TestRuntime><ApprovalTask approval={authoredApproval} onResolved={onResolved} reconcile={reconcile} /></TestRuntime>);
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Approved" } });

    fireEvent.click(screen.getAllByRole("button", { name: "Record decision" })[1]!);
    expect(await screen.findByText("Connection lost")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Record decision" })[1]!);

    await waitFor(() => expect(onResolved).toHaveBeenCalledOnce());
    expect(reconcile).toHaveBeenCalledWith("decision-1");
    expect(mocks.decide).toHaveBeenCalledTimes(2);
    expect(mocks.decide).toHaveBeenLastCalledWith({
      decision: "decision-1", verdict: "COMPLETE", payload: { action: "record", title: "Approved" },
    });
  });

  test("keeps an ambiguous approval open when reconciliation cannot read it", async () => {
    mocks.decide.mockRejectedValueOnce(new Error("Connection lost"));
    const reconcile = vi.fn(async () => null);
    const onResolved = vi.fn();
    render(<TestRuntime><ApprovalTask approval={authoredApproval} onResolved={onResolved} reconcile={reconcile} /></TestRuntime>);
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    const title = screen.getByLabelText("Title");
    fireEvent.change(title, { target: { value: "Approved" } });

    fireEvent.click(screen.getAllByRole("button", { name: "Record decision" })[1]!);
    expect(await screen.findByText("Connection lost")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Record decision" })[1]!);

    expect(await screen.findByText("This approval is unavailable or you no longer have access.")).toBeTruthy();
    expect((title as HTMLInputElement).value).toBe("Approved");
    expect(mocks.decide).toHaveBeenCalledOnce();
    expect(onResolved).not.toHaveBeenCalled();
  });

  test("reconciles invalid validation metadata and admits only one in-flight action", async () => {
    let settle: ((value: unknown) => void) | undefined;
    mocks.decide.mockImplementationOnce(() => new Promise((resolve) => { settle = resolve; }));
    const reconcile = vi.fn(async () => authoredApproval);
    render(<TestRuntime><ApprovalTask approval={authoredApproval} onResolved={() => undefined} reconcile={reconcile} /></TestRuntime>);

    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Approved" } });
    fireEvent.click(screen.getAllByRole("button", { name: "Record decision" })[1]!);
    await waitFor(() => expect(mocks.decide).toHaveBeenCalledOnce());
    fireEvent.click(screen.getAllByRole("button", { name: "Record decision" })[0]!);
    expect(mocks.decide).toHaveBeenCalledOnce();
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Record decision" })
      .every((button) => (button as HTMLButtonElement).disabled)).toBe(true));
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
    fireEvent.click(screen.getAllByRole("button", { name: "Record decision" })[1]!);
    await waitFor(() => expect(reconcile).toHaveBeenCalledWith("decision-1"));
    expect(mocks.decide).toHaveBeenCalledTimes(2);
  });

  test("preserves values for refreshes of one decision and resets them for another", () => {
    const { rerender } = render(<TestRuntime><ApprovalTask approval={authoredApproval} onResolved={() => undefined} /></TestRuntime>);
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Edited" } });

    rerender(<TestRuntime><ApprovalTask approval={{ ...authoredApproval, updated_at: "2026-09-08T08:02:00Z" }} onResolved={() => undefined} /></TestRuntime>);
    expect((screen.getByLabelText("Title") as HTMLInputElement).value).toBe("Edited");

    rerender(<TestRuntime><ApprovalTask approval={{ ...authoredApproval, id: "decision-2" }} onResolved={() => undefined} /></TestRuntime>);
    expect(screen.queryByLabelText("Title")).toBeNull();
  });

  test("keeps entered values read-only when refreshed to a terminal decision", () => {
    const { rerender } = render(<TestRuntime><ApprovalTask approval={authoredApproval} onResolved={() => undefined} /></TestRuntime>);
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Mine" } });

    rerender(<TestRuntime><ApprovalTask approval={{ ...authoredApproval, verdict: "EXPIRED",
      resolution: { action: "record", title: "Mine" } }} onResolved={() => undefined} /></TestRuntime>);

    expect(screen.getByText("This approval is no longer pending.")).toBeTruthy();
    expect(screen.getByText("Mine")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
  });

  test("renders only retained historical values without compiling an obsolete empty enum", () => {
    const widgets = {
      ...defaultWidgets,
      object: { read: ({ value }: { value?: unknown }) =>
        <span>{(value as { label?: string })?.label}</span> },
    };
    function HistoricalContent({ contextValues, values }: WorkflowDecisionContentProps) {
      return <div>{String((contextValues.review_context as { label?: string })?.label)} · {
        String(values.action)} · {String(values.title)}</div>;
    }
    render(<AppRuntimeProvider runtime={{ widgets, slots: [{
      id: "historical-review", slot: WORKFLOW_DECISION_CONTENT_SLOT,
      model: "workflows.Decision", impl: "review",
      content: HistoricalContent,
    }] }}><ApprovalTask approval={{
      ...approval,
      verdict: "COMPLETED",
      resolved_by: "workflows/cancel",
      payload: { review_context: { label: "Retained supplier context" } },
      resolution: { action: "record", title: "Retained history" },
      decision_schema: obsoleteHistoricalSchema,
    }} onResolved={() => undefined} /></AppRuntimeProvider>);

    expect(screen.getByText("Retained supplier context · record · Retained history")).toBeTruthy();
    expect(screen.queryByText("Default title")).toBeNull();
    expect(screen.queryByText(/Invalid Decision schema/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Record decision" })).toBeNull();
  });

  test("does not invent schema defaults for an expired unresolved decision", () => {
    render(<TestRuntime><ApprovalTask approval={{
      ...approval, verdict: "EXPIRED", decision_schema: obsoleteHistoricalSchema, resolution: {},
    }} onResolved={() => undefined} /></TestRuntime>);

    expect(screen.queryByText("Default title")).toBeNull();
    expect(screen.queryByRole("button", { name: "Record decision" })).toBeNull();
  });

  test("keeps obsolete empty enums invalid while a decision is pending", () => {
    render(<TestRuntime><ApprovalTask approval={{ ...approval, decision_schema: obsoleteHistoricalSchema }}
      onResolved={() => undefined} /></TestRuntime>);

    expect(screen.getByText(/enum must have non-empty array/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Record decision" })).toBeNull();
  });

  test("uses the calm history fallback when a terminal decision has no action schema", () => {
    render(<TestRuntime><ApprovalTask approval={{
      ...approval, verdict: "EXPIRED", decision_schema: null,
    }} onResolved={() => undefined} /></TestRuntime>);

    expect(screen.getByText(/This historical approval uses an older form format/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("keeps pending values read-only when exact reconciliation becomes unavailable", () => {
    const { rerender } = render(<TestRuntime><ApprovalTask approval={authoredApproval} onResolved={() => undefined} /></TestRuntime>);
    fireEvent.click(screen.getByRole("button", { name: "Record decision" }));
    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Retain" } });

    rerender(<TestRuntime><ApprovalTask approval={authoredApproval} available={false} onResolved={() => undefined} /></TestRuntime>);

    expect(screen.getByText("This approval is unavailable or you no longer have access.")).toBeTruthy();
    expect(screen.getByText("Retain")).toBeTruthy();
    expect(screen.queryByRole("textbox", { name: "Title" })).toBeNull();
  });

  test("disables authored actions while a resolution is pending", () => {
    mocks.mutationState.fetching = true;
    render(<TestRuntime><ApprovalTask approval={authoredApproval} onResolved={() => undefined} /></TestRuntime>);
    expect((screen.getByRole("button", { name: "Record decision" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

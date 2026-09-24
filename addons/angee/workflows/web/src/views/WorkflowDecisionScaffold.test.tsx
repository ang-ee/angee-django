// @vitest-environment happy-dom

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRuntimeProvider, baseIcons, defaultWidgets } from "@angee/ui";
import * as v from "valibot";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiRouteTestDoubles, createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, { ...createUiRouteTestDoubles(), useMediaQuery: () => true });
});
vi.mock("../i18n", () => ({ useWorkflowsT: () => (key: string) => key }));

import { DecisionField, type WorkflowDecisionContentProps } from "./ApprovalTask";
import { NativeWorkflowDecisionScaffold, WorkflowDecisionScaffold, type WorkflowDecisionContext, type WorkflowDecisionReference } from "./WorkflowDecisionScaffold";
import { decisionContextWidgets, decisionReviewFact } from "./DecisionContextWidgets";

const ContextSchema = v.object({ title: v.string(), warning: v.string(), recordId: v.string() });

afterEach(cleanup);

test("selects an exact native fact, without interpreting its pointer or accepting ambiguous keys", () => {
  const fact = { pointer: "/review_context", label: "Retained review", authority: "source", value: { title: "Invoice" } };
  expect(decisionReviewFact([fact], "/review_context")?.value).toEqual({ title: "Invoice" });
  expect(decisionReviewFact([fact], "/review_context/title")).toBeUndefined();
  expect(decisionReviewFact([fact, fact], "/review_context")).toBeUndefined();
  expect(decisionReviewFact([{ ...fact, authority: "trusted" }], "/review_context")).toBeUndefined();
});

test("renders the frozen-context shell, reference row, warning, and owned action placement", () => {
  const props = decisionProps({ title: "Retained review", warning: "Check this", recordId: "record-1" });
  render(<WorkflowDecisionScaffold
    props={props}
    context={props.contextValues.review_context}
    schema={ContextSchema}
    header={(context) => ({ eyebrow: "Review", title: context.title, description: "Description" })}
    warning={(context) => ({ title: "Warning", description: context.warning })}
    references={(context) => [{
      label: "Open source",
      reference: { model: "notes.Note", id: context.recordId },
    }]}
    actionPickerPlacement="before-content"
  >{() => <div>Domain content</div>}</WorkflowDecisionScaffold>);

  expect(screen.getByRole("heading", { name: "Retained review" })).toBeTruthy();
  expect(screen.getByText("Check this")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Open source" }).getAttribute("href"))
    .toBe("/records/notes.Note/record-1");
  expect(screen.getByText("Decision picker").compareDocumentPosition(screen.getByText("Domain content")))
    .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
});

test("invalid retained context keeps the framework action picker available", () => {
  render(<WorkflowDecisionScaffold
    props={decisionProps({ title: 12 })}
    context={{ title: 12 }}
    schema={ContextSchema}
    header={(context) => ({ eyebrow: "Review", title: context.title, description: "Description" })}
    actionPickerPlacement="after-content"
  >{() => <div>Domain content</div>}</WorkflowDecisionScaffold>);

  expect(screen.getByText("inbox.contextUnavailableTitle")).toBeTruthy();
  expect(screen.getByText("Decision picker")).toBeTruthy();
  expect(screen.queryByText("Domain content")).toBeNull();
});

test("builds references once and selects the initial peek from the rendered references", () => {
  const context = { title: "Retained review", warning: "", recordId: "file-1" };
  const props = { ...decisionProps(context), openEvidence: vi.fn() };
  const references = vi.fn((review: v.InferOutput<typeof ContextSchema>): WorkflowDecisionReference[] => [{
    label: "Open evidence",
    kind: "evidence",
    reference: { model: "storage.File", id: review.recordId, page: 3 },
  }]);
  const initialPeek = vi.fn((
    _review: v.InferOutput<typeof ContextSchema>,
    actions: readonly WorkflowDecisionReference[],
  ) => actions.find((action) => action.kind === "evidence"));
  render(<WorkflowDecisionScaffold
    props={props}
    context={context}
    schema={ContextSchema}
    header={(review) => ({ eyebrow: "Review", title: review.title, description: "Description" })}
    references={references}
    initialPeek={initialPeek}
  >{() => <div>Domain content</div>}</WorkflowDecisionScaffold>);

  expect(references).toHaveBeenCalledTimes(1);
  expect(initialPeek).toHaveBeenCalledTimes(1);
  const renderedReferences = references.mock.results[0]!.value;
  expect(initialPeek.mock.calls[0]![1]).toBe(renderedReferences);
  expect(props.openEvidence).toHaveBeenCalledExactlyOnceWith(
    renderedReferences[0]!.reference, { tabActivation: "initial" },
  );
  fireEvent.click(screen.getByRole("button", { name: "Open evidence" }));
  expect(props.openEvidence).toHaveBeenLastCalledWith({
    ...renderedReferences[0]!.reference, label: "Open evidence",
  });
});

test.each(["single", "list"] as const)("renders native facts and %s references, and peeks at the first retained target once", (shape) => {
  const reference = { model: "notes.Note", id: "retained-1", label: "Source note", tab: "history", page: 2, search: { query: "retained" } };
  const props: WorkflowDecisionContentProps = {
    ...decisionProps(undefined),
    contextFields: [{ name: "facts", widget: "facts" }, { name: "references", widget: "record" }],
    contextValues: {
      facts: [{ pointer: "/summary", label: "Frozen facts", value: "Retained text", authority: "source" }],
      references: shape === "single" ? reference : [reference, { model: "notes.Note", id: "retained-2" }],
    },
    openEvidence: vi.fn(),
  };
  const content = <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: { ...defaultWidgets, ...decisionContextWidgets } }}>
    <NativeWorkflowDecisionScaffold props={props}
      header={{ eyebrow: "Review", title: "Native review", description: "Review retained context" }}
      contextDetails={{ title: "Evidence", description: "Frozen evidence", label: "Details" }}
      actionPickerPlacement="before-content"
    ><div>Correction controls</div></NativeWorkflowDecisionScaffold>
  </AppRuntimeProvider>;
  const { rerender } = render(content);

  expect(screen.getByRole("heading", { name: "Native review" })).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Evidence" })).toBeTruthy();
  expect(screen.queryByText("Frozen facts")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Details" }));
  expect(screen.getByText("Frozen facts")).toBeTruthy();
  expect(screen.getByText("Retained text")).toBeTruthy();
  expect(props.openEvidence).toHaveBeenCalledWith(reference, { tabActivation: "initial" });
  rerender(content);
  expect(props.openEvidence).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Decision picker").compareDocumentPosition(screen.getByText("Correction controls")))
    .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
});

test("native summaries and reference actions share the parsed context and initial peek", () => {
  const props = {
    ...decisionProps(undefined),
    contextValues: { facts: [{ pointer: "/title", label: "Title", value: "Retained title", authority: "source" }] },
    openEvidence: vi.fn(),
  };
  const summary = vi.fn((context: WorkflowDecisionContext) => <div>{String(context.facts?.[0]?.value)}</div>);
  const references = vi.fn((_context: WorkflowDecisionContext): WorkflowDecisionReference[] => [{
    label: "Open source", kind: "evidence", reference: { model: "storage.File", id: "file-1" },
  }]);
  const initialPeek = vi.fn((_context: WorkflowDecisionContext, actions: readonly WorkflowDecisionReference[]) => actions[0]);
  render(<NativeWorkflowDecisionScaffold props={props}
    header={{ eyebrow: "Review", title: "Native review", description: "Description" }}
    contextDetails={{ title: "Evidence", label: "Details" }}
    summary={summary} references={references} initialPeek={initialPeek}
  ><div>Correction controls</div></NativeWorkflowDecisionScaffold>);

  expect(screen.getByText("Retained title")).toBeTruthy();
  expect(references).toHaveBeenCalledTimes(1);
  expect(summary).toHaveBeenCalledWith(references.mock.calls[0]![0]);
  expect(initialPeek).toHaveBeenCalledWith(summary.mock.calls[0]![0], references.mock.results[0]!.value);
  expect(props.openEvidence).toHaveBeenCalledExactlyOnceWith(
    references.mock.results[0]!.value[0]!.reference, { tabActivation: "initial" },
  );
  fireEvent.click(screen.getByRole("button", { name: "Open source" }));
  expect(props.openEvidence).toHaveBeenLastCalledWith({ model: "storage.File", id: "file-1", label: "Open source" });
});

test.each([
  {},
  { references: [{ model: "notes.Note", id: 12 }] },
  { facts: [{ pointer: "/summary", label: "Facts", value: "Bad authority", authority: "trusted" }] },
])("missing or malformed native context retains the input controls and action picker", (contextValues) => {
  const props = { ...decisionProps(undefined), contextValues, openEvidence: vi.fn() };
  render(<NativeWorkflowDecisionScaffold props={props}
    header={{ eyebrow: "Review", title: "Native review", description: "Description" }}
    contextDetails={{ title: "Evidence", label: "Details" }}
    actionPickerPlacement="before-content"
  ><div>Correction controls</div></NativeWorkflowDecisionScaffold>);

  expect(screen.getByText("inbox.contextUnavailableTitle")).toBeTruthy();
  expect(screen.getByText("Decision picker")).toBeTruthy();
  expect(screen.getByText("Correction controls")).toBeTruthy();
  expect(props.openEvidence).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Details" })).toBeNull();
});

test("DecisionField selects a registered widget while preserving native values and errors", () => {
  const props: WorkflowDecisionContentProps = {
    ...decisionProps(undefined),
    inputFields: [{ name: "note", label: "Review note", widget: "text" }],
    values: { note: "Retained note" },
    messagesFor: () => ["Explain this change"],
  };
  render(<AppRuntimeProvider runtime={{ widgets: defaultWidgets }}>
    <DecisionField props={props} name="note" widget="textarea" />
  </AppRuntimeProvider>);

  const control = screen.getByRole("textbox", { name: "Review note" });
  expect(control.tagName).toBe("TEXTAREA");
  expect((control as HTMLTextAreaElement).value).toBe("Retained note");
  expect(screen.getByText("Explain this change")).toBeTruthy();
  fireEvent.change(control, { target: { value: "Reviewed note" } });
  expect(props.setValue).toHaveBeenCalledWith("note", "Reviewed note");
});

function decisionProps(reviewContext: unknown): WorkflowDecisionContentProps {
  return {
    approval: { id: "decision-1" } as WorkflowDecisionContentProps["approval"],
    contextFields: [],
    contextValues: { review_context: reviewContext },
    inputFields: [],
    values: {},
    setValue: vi.fn(),
    messagesFor: () => [],
    actionPicker: <div>Decision picker</div>,
    selectAction: vi.fn(),
    editable: true,
    fetching: false,
    readOnly: false,
  };
}

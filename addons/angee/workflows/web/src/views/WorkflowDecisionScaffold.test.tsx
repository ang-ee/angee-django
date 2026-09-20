// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import * as v from "valibot";
import { afterEach, expect, test, vi } from "vitest";

vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiRouteTestDoubles, createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, createUiRouteTestDoubles());
});
vi.mock("../i18n", () => ({ useWorkflowsT: () => (key: string) => key }));

import type { WorkflowDecisionContentProps } from "./ApprovalTask";
import {
  WorkflowDecisionScaffold,
  presentRows,
  reasonLabel,
  textValue,
  unique,
} from "./WorkflowDecisionScaffold";

const ContextSchema = v.object({ title: v.string(), warning: v.string(), recordId: v.string() });

afterEach(cleanup);

test("renders the frozen-context shell, reference row, warning, and owned action placement", () => {
  const props = decisionProps({ title: "Retained review", warning: "Check this", recordId: "record-1" });
  render(<WorkflowDecisionScaffold
    props={props}
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
    schema={ContextSchema}
    header={(context) => ({ eyebrow: "Review", title: context.title, description: "Description" })}
    actionPickerPlacement="after-content"
  >{() => <div>Domain content</div>}</WorkflowDecisionScaffold>);

  expect(screen.getByText("inbox.contextUnavailableTitle")).toBeTruthy();
  expect(screen.getByText("Decision picker")).toBeTruthy();
  expect(screen.queryByText("Domain content")).toBeNull();
});

test("shared retained-value helpers preserve intent without private copies", () => {
  expect(presentRows([["kept", 0], ["empty", ""]] as const)).toEqual([["kept", 0]]);
  expect(textValue("  value  ")).toBe("value");
  expect(textValue(12)).toBe("12");
  expect(reasonLabel((key) => key === "decision.reasonCode.match" ? "Matched" : key, "match"))
    .toBe("Matched");
  expect(unique(["a", "", "a", "b"])).toEqual(["a", "b"]);
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

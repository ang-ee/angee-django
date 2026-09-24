// @vitest-environment happy-dom

import * as React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  data: {
    workflow_subject_history: {
      truncated: false,
      runs_truncated: false,
      decisions_truncated: false,
      runs: [
        {
          id: "run-waiting",
          status: "WAITING",
          origin: "TEST",
          waiting_kind: "EXTERNAL",
          next_wake_at: null,
          active_step: "Prepare counterparty review",
          updated_at: "2026-09-18T10:00:00Z",
          workflow: { id: "workflow-waiting", name: "Counterparty review" },
          parent_step_run: null,
          reprocessed_from: null,
          recovery_source_attempt: null,
        },
        {
          id: "run-complete",
          status: "SUCCEEDED",
          origin: "TEST",
          waiting_kind: null,
          next_wake_at: null,
          active_step: null,
          updated_at: "2026-09-18T09:00:00Z",
          workflow: { id: "workflow-complete", name: "Completed workflow" },
          parent_step_run: null,
          reprocessed_from: null,
          recovery_source_attempt: null,
        },
      ],
      child_runs: [],
      failures: [],
      decisions: [],
      artifacts: [
        {
          id: "artifact-current-readable",
          label: "Counterparty eligibility needs confirmation",
          created_at: "2026-09-18T10:01:00Z",
          target_reference: { model: "example.CounterpartyProfile", id: "counterparty-1" },
          attempt: {
            id: "attempt-current",
            step_run: {
              id: "step-run-waiting",
              status: "WAITING",
              waiting_kind: "EXTERNAL",
              run: { id: "run-waiting" },
              current_attempt: { id: "attempt-current" },
            },
          },
        },
        {
          id: "artifact-current-unreadable",
          label: "Claimed sender association",
          created_at: "2026-09-18T10:01:00Z",
          target_reference: null,
          attempt: {
            id: "attempt-current",
            step_run: {
              id: "step-run-waiting",
              status: "WAITING",
              waiting_kind: "EXTERNAL",
              run: { id: "run-waiting" },
              current_attempt: { id: "attempt-current" },
            },
          },
        },
        {
          id: "artifact-stale",
          label: "Earlier counterparty evidence",
          created_at: "2026-09-18T09:58:00Z",
          target_reference: { model: "example.CounterpartyProfile", id: "counterparty-old" },
          attempt: {
            id: "attempt-earlier",
            step_run: {
              id: "step-run-waiting",
              status: "WAITING",
              waiting_kind: "EXTERNAL",
              run: { id: "run-waiting" },
              current_attempt: { id: "attempt-current" },
            },
          },
        },
        {
          id: "artifact-complete",
          label: "Completed output",
          created_at: "2026-09-18T09:01:00Z",
          target_reference: { model: "example.Document", id: "document-1" },
          attempt: {
            id: "attempt-complete",
            step_run: {
              id: "step-run-complete",
              status: "SUCCEEDED",
              waiting_kind: null,
              run: { id: "run-complete" },
              current_attempt: { id: "attempt-complete" },
            },
          },
        },
      ],
    },
  },
}));

vi.mock("@angee/refine", async (importOriginal) => ({
  ...await importOriginal<typeof import("@angee/refine")>(),
  useAuthoredQuery: () => ({ data: mocks.data, isFetching: false, error: null }),
}));

vi.mock("@angee/iam", () => ({
  useAssignmentSubjects: () => ({ options: [] }),
}));

vi.mock("@angee/ui", async (importOriginal) => {
  const { createUiRouteTestDoubles, createUiTestModule } = await import("@angee/ui/testing");
  return createUiTestModule(importOriginal, createUiRouteTestDoubles());
});

vi.mock("../documents.console", () => ({
  WorkflowSubjectHistoryPaneDocument: {},
}));

vi.mock("../i18n", () => ({
  useWorkflowsT: () => (key: string, values?: Record<string, unknown>) => ({
    "subjectHistory.activeStep": `Current step: ${String(values?.step ?? "")}`,
    "subjectHistory.waiting": `Waiting: ${String(values?.reason ?? "")}`,
    "subjectHistory.waitingForUpdate": "Waiting for an update",
    "subjectHistory.outputs": "Outputs",
    "subjectHistory.outputUnavailable": "Output unavailable",
  }[key] ?? key),
}));

vi.mock("./WorkflowApprovals", () => ({
  WorkflowApprovals: () => null,
}));

import { WorkflowSubjectHistoryPane } from "./WorkflowSubjectHistoryPane";

afterEach(cleanup);

test("current external-wait artifacts become one routed next-action block while historical outputs stay retained", () => {
  render(<WorkflowSubjectHistoryPane subjectDeclaration="example.Document" subjectId="document-1" />);

  const waitingBlock = screen.getByText("Waiting for an update").parentElement;
  expect(waitingBlock).not.toBeNull();
  expect(within(waitingBlock!).getByRole("link", {
    name: "Counterparty eligibility needs confirmation",
  }).getAttribute("href")).toBe("/records/example.CounterpartyProfile/counterparty-1");
  expect(within(waitingBlock!).getByText("Claimed sender association").closest("a")).toBeNull();

  const outputs = screen.getByRole("heading", { name: "Outputs" }).closest("section");
  expect(outputs).not.toBeNull();
  expect(within(outputs!).getByRole("link", { name: "Earlier counterparty evidence" })).toBeTruthy();
  expect(within(outputs!).getByRole("link", { name: "Completed output" })).toBeTruthy();
  expect(within(outputs!).queryByText("Counterparty eligibility needs confirmation")).toBeNull();
  expect(screen.getAllByText("Counterparty eligibility needs confirmation")).toHaveLength(1);
  expect(screen.getAllByText("Waiting for an update")).toHaveLength(1);
});

test("collapsible presentation keeps subject history inside the shared bounded activity pane", () => {
  render(<WorkflowSubjectHistoryPane
    subjectDeclaration="agents.AgentSession"
    subjectId="session-1"
    presentation="collapsible"
  />);

  expect(screen.getByText("inbox.title")).toBeTruthy();
  expect(screen.getByText("Counterparty review").closest("[class*='max-h']")).toBeTruthy();
});

// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AppRuntimeProvider, CHATTER_CONTAINERS, RelatedCountProbe, containersFromChildren, type ChatterViewContext } from "@angee/ui";
import type { RunRelatedQuery } from "@angee/gql/console/graphql";

const query = vi.hoisted(() => vi.fn());
vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()), useAuthoredQuery: query,
}));

import { DecisionRunCountDocument, DecisionStepsDocument, RecordRelatedRunsDocument, RunDecisionsCountDocument, RunRelatedDocument } from "./documents.console";
import { decisionRunGroup, recordRunsGroup, runDecisionsGroup, runRecords, runRecordsGroup } from "./related";

afterEach(() => { cleanup(); query.mockReset(); });

const context = (model: string): ChatterViewContext => ({
  pathname: "/records/rec_1", params: { id: "rec_1" },
  route: { name: "record", path: "/records/$id", viewType: "record", modelLabel: model },
  view: { kind: "record", type: "record", sqid: "rec_1" },
});

test("a run's Decisions badge selects only the step aggregate", () => {
  query.mockReturnValue({ data: { steprun_aggregate: { aggregate: { count: 2 } } } });
  render(<AppRuntimeProvider runtime={{ containers: containersFromChildren(CHATTER_CONTAINERS, {
    "workflows.WorkflowRun#groups": { decisions: runDecisionsGroup },
  }) }}><RelatedCountProbe id="related" context={context("workflows.WorkflowRun")} onCount={vi.fn()} /></AppRuntimeProvider>);
  expect(query).toHaveBeenCalledWith(RunDecisionsCountDocument, { run: "rec_1" }, expect.anything());
  expect(query).not.toHaveBeenCalledWith(RunRelatedDocument, expect.anything(), expect.anything());
});

test("a decision's Run badge selects only the step aggregate", () => {
  query.mockReturnValue({ data: { steprun_aggregate: { aggregate: { count: 1 } } } });
  render(<AppRuntimeProvider runtime={{ containers: containersFromChildren(CHATTER_CONTAINERS, {
    "decisions.Decision#groups": { run: decisionRunGroup },
  }) }}><RelatedCountProbe id="related" context={context("decisions.Decision")} onCount={vi.fn()} /></AppRuntimeProvider>);
  expect(query).toHaveBeenCalledWith(DecisionRunCountDocument, { decision: "rec_1" }, expect.anything());
  expect(query).not.toHaveBeenCalledWith(DecisionStepsDocument, expect.anything(), expect.anything());
});

test("timeline-derived Runs count waits for the Links pane", () => {
  render(<AppRuntimeProvider runtime={{ containers: containersFromChildren(CHATTER_CONTAINERS, {
    "related#groups": { runs: recordRunsGroup },
  }) }}><RelatedCountProbe id="related" context={context("example.Record")} onCount={vi.fn()} /></AppRuntimeProvider>);
  expect(query).not.toHaveBeenCalledWith(RecordRelatedRunsDocument, expect.anything(), expect.anything());
});

test("a run's derived distinct Records count waits for the Links pane", () => {
  render(<AppRuntimeProvider runtime={{ containers: containersFromChildren(CHATTER_CONTAINERS, {
    "workflows.WorkflowRun#groups": { records: runRecordsGroup },
  }) }}><RelatedCountProbe id="related" context={context("workflows.WorkflowRun")} onCount={vi.fn()} /></AppRuntimeProvider>);
  expect(query).not.toHaveBeenCalledWith(RunRelatedDocument, expect.anything(), expect.anything());
});

test("run records use the generated operation enum and keep the strongest role", () => {
  const record = (id: string, operation: RunRelatedQuery["steprecord"][number]["operation"]) => ({
    id: `step_${id}_${operation}`, label: id, operation, record_model: "notes.Note", record_id: id,
    step_run: { id: `run_${id}`, node_label: operation },
  });
  const data: RunRelatedQuery = {
    workflowrun_by_pk: { id: "run_1", subject_model: null, subject_id: null, trigger_event: null },
    steprecord: [record("created", "CREATED"), record("changed", "CHANGED"), record("read", "READ"),
      record("created", "READ"), record("called", "CALLED")],
    steprun: [],
  };
  expect(runRecords(data).map(({ id, role }) => [id, role])).toEqual([
    ["created", "created"], ["changed", "changed"], ["read", "read"], ["called", "read"],
  ]);
});

// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AppRuntimeProvider, CHATTER_CONTAINERS, RelatedCountProbe, containersFromChildren, type ChatterViewContext } from "@angee/ui";

const query = vi.hoisted(() => vi.fn());
vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()), useAuthoredQuery: query,
}));

import { DecisionRunCountDocument, DecisionStepsDocument, RecordRelatedRunsDocument, RunDecisionsCountDocument, RunRelatedDocument } from "./documents.console";
import { decisionRunGroup, recordRunsGroup, runDecisionsGroup, runRecordsGroup } from "./related";

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

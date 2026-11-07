// @vitest-environment happy-dom

import { cleanup, render } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { AppRuntimeProvider, CHATTER_CONTAINERS, RelatedCountProbe, containersFromChildren, type ChatterViewContext } from "@angee/ui";

const query = vi.hoisted(() => vi.fn());
vi.mock("@angee/refine", async (original) => ({
  ...(await original<typeof import("@angee/refine")>()), useAuthoredQuery: query,
}));

import { DecisionStepsDocument, RecordRelatedRunsDocument, RunRelatedDocument } from "./documents.console";
import { decisionRunGroup, recordRunsGroup, runDecisionsGroup, runRecordsGroup } from "./related";

afterEach(() => { cleanup(); query.mockReset(); });

const context = (model: string): ChatterViewContext => ({
  pathname: "/records/rec_1", params: { id: "rec_1" },
  route: { name: "record", path: "/records/$id", viewType: "record", modelLabel: model },
  view: { kind: "record", type: "record", sqid: "rec_1" },
});

const groupCount = (group: string, count: number) => ({ data: { related_counts: [{ group, count }] } });

test("a run's Decisions badge counts its declared related count without the run's related read", () => {
  query.mockReturnValue(groupCount("workflows.decisions", 2));
  const onCount = vi.fn();
  render(<AppRuntimeProvider runtime={{ containers: containersFromChildren(CHATTER_CONTAINERS, {
    "workflows.WorkflowRun#groups": { "workflows.decisions": runDecisionsGroup },
  }) }}><RelatedCountProbe id="related" context={context("workflows.WorkflowRun")} onCount={onCount} /></AppRuntimeProvider>);
  expect(query).toHaveBeenCalledWith(expect.anything(), { model: "workflows.WorkflowRun", id: "rec_1", groups: ["workflows.decisions"] }, expect.anything());
  expect(query).not.toHaveBeenCalledWith(RunRelatedDocument, expect.anything(), expect.anything());
  expect(onCount).toHaveBeenLastCalledWith("related", 2);
});

test("a decision's Run badge counts its declared related count without the decision's steps", () => {
  query.mockReturnValue(groupCount("workflows.run", 1));
  render(<AppRuntimeProvider runtime={{ containers: containersFromChildren(CHATTER_CONTAINERS, {
    "decisions.Decision#groups": { "workflows.run": decisionRunGroup },
  }) }}><RelatedCountProbe id="related" context={context("decisions.Decision")} onCount={vi.fn()} /></AppRuntimeProvider>);
  expect(query).toHaveBeenCalledWith(expect.anything(), { model: "decisions.Decision", id: "rec_1", groups: ["workflows.run"] }, expect.anything());
  expect(query).not.toHaveBeenCalledWith(DecisionStepsDocument, expect.anything(), expect.anything());
});

test("a record's Runs badge counts its declared related count without the timeline read", () => {
  query.mockReturnValue(groupCount("workflows.runs", 3));
  const onCount = vi.fn();
  render(<AppRuntimeProvider runtime={{ containers: containersFromChildren(CHATTER_CONTAINERS, {
    "related#groups": { "workflows.runs": recordRunsGroup },
  }) }}><RelatedCountProbe id="related" context={context("example.Record")} onCount={onCount} /></AppRuntimeProvider>);
  expect(query).toHaveBeenCalledWith(expect.anything(), { model: "example.Record", id: "rec_1", groups: ["workflows.runs"] }, expect.anything());
  expect(query).not.toHaveBeenCalledWith(RecordRelatedRunsDocument, expect.anything(), expect.anything());
  expect(onCount).toHaveBeenLastCalledWith("related", 3);
});

test("a run's derived distinct Records count waits for the Links pane", () => {
  render(<AppRuntimeProvider runtime={{ containers: containersFromChildren(CHATTER_CONTAINERS, {
    "workflows.WorkflowRun#groups": { "workflows.records": runRecordsGroup },
  }) }}><RelatedCountProbe id="related" context={context("workflows.WorkflowRun")} onCount={vi.fn()} /></AppRuntimeProvider>);
  expect(query).not.toHaveBeenCalledWith(RunRelatedDocument, expect.anything(), expect.anything());
});

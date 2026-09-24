import { expect, test } from "vitest";
import { updateRouteSearch } from "@angee/ui";

import { decisionHref, decisionSearchPatch, subjectDecisionRunId, subjectPendingDecision, workflowSubjectActionSearchPatch } from "../decision-navigation";

test("admits a Decision only from the canonical direct or artifact-related subject history", () => {
  const runs = [{ id: "direct-run" }, { id: "artifact-related-run" }];

  expect(subjectDecisionRunId("artifact-related-run", runs)).toBe("artifact-related-run");
  expect(subjectDecisionRunId("unrelated-run", runs)).toBeNull();
});

test("a successful subject action clears stale Decision selection and follows its WorkflowRun", () => {
  expect(updateRouteSearch(workflowSubjectActionSearchPatch("run-new"))({ decision: "old", page: 2 })).toEqual({
    chatterTab: "workflows", decision: undefined, page: 2, workflowRun: "run-new",
  });
});

test("Decision selection and clearing use the same route-search patch convention", () => {
  const selected = updateRouteSearch(decisionSearchPatch("decision-new"))({ workflowRun: "old", page: 2 });
  expect(selected).toEqual({ chatterTab: "workflows", decision: "decision-new", workflowRun: undefined, page: 2 });
  expect(updateRouteSearch(decisionSearchPatch(null))(selected)).toEqual({
    chatterTab: undefined, decision: undefined, workflowRun: undefined, page: 2,
  });
});

test("a followed Run without a Decision does not expose an older pending task", () => {
  const pending = [{ id: "old", step_run: { run: { id: "run-old" } } }];
  expect(subjectPendingDecision(pending, "run-new")).toBeUndefined();
  expect(subjectPendingDecision(pending, null)).toBe(pending[0]);
});

test("a Decision target opens the shared Workflows chatter tab", () => {
  expect(decisionHref("/example/entries/entry-1", "decision-1")).toBe(
    "/example/entries/entry-1?chatterTab=workflows&decision=decision-1",
  );
});

import { expectValidBaseAddon } from "@angee/app/testing";
import { expect, test } from "vitest";
import decisions, { DECISION_MODEL, decisionRecordTab } from "./index";

test("decisions own their route and record verbs", () => {
  expect(() => expectValidBaseAddon(decisions)).not.toThrow();
  expect(decisions.routes?.map(({ name }) => name)).toEqual(["decisions.inbox", "decisions.inbox.record"]);
  expect(decisions.slots).toEqual([expect.objectContaining({ model: DECISION_MODEL, slot: "form-view.record-actions" })]);
});

test("subject tabs are generic and have distinct contribution identities", () => {
  const first = decisionRecordTab("projects.Task");
  const second = decisionRecordTab("projects.Project");
  expect(first.model).toBe("projects.Task");
  expect(first.slot).toBe("form-view.sections");
  expect(first.id).not.toBe(second.id);
});

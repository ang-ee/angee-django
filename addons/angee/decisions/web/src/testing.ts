import type { Decision } from "./documents.console";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";

export const decisionResourceFixture = testDataResource("decisions.Decision", {
  capabilities: ["list", "detail"], recordRepresentation: "kind_label",
  fields: [{ name: "assignees", kind: "list", scalar: null, relationModelLabel: "iam.User", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
  roots: { list: "decisions", detail: "decisions_by_pk", aggregate: "decisions_aggregate" },
  typeNames: { filter: "decisions_bool_exp", order: "decisions_order_by" },
  query: testResourceQuery({ fields: {
    ...Object.fromEntries(["id", "kind", "kind_label", "proposal", "records", "requester.display_name",
      "assignees", "requester", "answered_by", "answered_at", "answered_by.display_name", "revision", "permissions",
      "context", "verdict"].map((name) => [name, testQueryField(name)])),
    id: testQueryField("id", { filter: { field: "id", scalar: "String", values: [], operators: ["exact", "ne", "inList"] } }),
    assignees: testQueryField("assignees", { kind: "list", scalar: null, row: null }),
    created_at: testQueryField("created_at", { sort: { field: "created_at" } }),
    is_open: testQueryField("is_open", { scalar: "Boolean", nullable: false,
      filter: { field: "is_open", scalar: "Boolean", values: [], operators: ["exact"] } }),
    can_act: testQueryField("can_act", { scalar: "Boolean", nullable: false,
      filter: { field: "can_act", scalar: "Boolean", values: [], operators: ["exact"] } }),
  } }),
});

export const decisionUserFixture = testDataResource("iam.User", {
  recordRepresentation: "display_name", capabilities: ["detail"], roots: { detail: "user_by_pk" },
  fields: [{ name: "display_name", kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
});

export function decisionFixture(overrides: Partial<Decision> = {}): Decision {
  return {
    id: "dcn_review", kind: "review", kind_label: "Review", revision: 3, created_at: "2026-10-03T10:00:00Z", is_open: true, permissions: ["act"],
    context: { facts: [], references: [] }, verdict: null, answered_at: null,
    records: [{ id: "dcr_7", record_model: "notes.Note", record_id: "nte_7" }],
    proposal: { multiple: false, alternatives: [
      { key: "accept", label: "Accept", outcome: "accepted", actions: {
        nte_7: { fields: { display_name: { set: "Proposed name" } }, record: { call: "archive" } },
      } },
      { key: "reject", label: "Keep what is on the record", outcome: "rejected" },
    ] },
    requester: { display_name: "Requester" }, assignees: [{ display_name: "Reviewer" }], answered_by: null, ...overrides,
  };
}

export const decisionRecordFixture = testDataResource("notes.Note", {
  capabilities: ["list", "detail"], recordRepresentation: "display_name",
  query: testResourceQuery({ fields: { id: testQueryField("id"), display_name: testQueryField("display_name") } }),
});

export const decisionLinkFixture = testDataResource("decisions.DecisionRecord", {
  capabilities: ["list", "detail"], roots: { list: "decision_records", detail: "decision_records_by_pk" },
});

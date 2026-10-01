import type { Decision } from "./documents.console";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";

const verdicts = [
  { value: "PENDING", description: "Pending" }, { value: "COMPLETED", description: "Completed" },
  { value: "REJECTED", description: "Rejected" },
];
const closedReasons = [
  { value: "RESOLVED", description: "Resolved" }, { value: "SIBLING_SETTLED", description: "Sibling settled" },
];

export const decisionGroupFixture = testDataResource("decisions.DecisionGroup", {
  capabilities: ["list", "detail"], roots: { list: "decision_groups", detail: "decision_groups_by_pk" },
});

/** Executable resource metadata for inbox tests and interactive decision stories. */
export const decisionResourceFixture = testDataResource("decisions.Decision", {
  capabilities: ["list", "detail"],
  recordRepresentation: "kind_label",
  fields: [{ name: "verdict", kind: "enum", scalar: "String", values: verdicts, readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
  }, { name: "closed_reason", kind: "enum", scalar: "String", values: closedReasons, readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
  }, { name: "assignees", kind: "list", scalar: null, relationModelLabel: "iam.User", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
  }],
  roots: { list: "decisions", detail: "decisions_by_pk", aggregate: "decisions_aggregate" },
  typeNames: { filter: "decisions_bool_exp", order: "decisions_order_by" },
  query: testResourceQuery({ fields: {
    ...Object.fromEntries([
      "id", "kind", "kind_label", "subject_model", "subject_id", "requester.display_name",
      "assignees", "requester", "closed_reason", "expires_at", "group", "group.id",
      "resolved_by", "resolved_at", "resolved_by.display_name", "revision", "permissions", "form_schema", "basis", "context", "resolution",
    ].map((name) => [name, testQueryField(name)])),
    id: testQueryField("id", { filter: { field: "id", scalar: "String", values: [], operators: ["exact", "ne", "inList"] } }),
    assignees: testQueryField("assignees", { kind: "list", scalar: null, row: null }),
    verdict: testQueryField("verdict", { values: verdicts }),
    index: testQueryField("index", { scalar: "Int", sort: { field: "index" } }),
    created_at: testQueryField("created_at", { sort: { field: "created_at" } }),
    is_open: testQueryField("is_open", { scalar: "Boolean", nullable: false,
      filter: { field: "is_open", scalar: "Boolean", values: [], operators: ["exact"] },
    }),
  } }),
});

export const decisionUserFixture = testDataResource("iam.User", {
  recordRepresentation: "display_name",
  capabilities: ["detail"], roots: { detail: "user_by_pk" },
  fields: [{ name: "display_name", kind: "scalar", scalar: "String", readable: true,
    aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false }],
});

/** Frozen neutral question shared by decision page, slot, and story fixtures. */
export function decisionFixture(overrides: Partial<Decision> = {}): Decision {
  return {
    id: "dcn_review", kind: "review", revision: 3, is_open: true, permissions: ["act"],
    form_schema: {
      $schema: "https://json-schema.org/draft/2020-12/schema", type: "object",
      properties: { action: { type: "string", enum: ["accept", "reject"], options: [
        { value: "accept", label: "Accept", verdict: "completed" },
        { value: "reject", label: "Reject", verdict: "rejected" },
      ] } },
      required: ["action"], discriminator: { propertyName: "action" },
      oneOf: [
        { type: "object", additionalProperties: false, required: ["action", "reference"], properties: {
          action: { type: "string", const: "accept" },
          note: { type: "string", title: "Note", default: "Read" },
          reference: { type: "string", title: "Reference", default: "R-7", const: "R-7", readOnly: true },
        } },
        { type: "object", additionalProperties: false, required: ["action", "reason"], properties: {
          action: { type: "string", const: "reject" },
          reason: { type: "string", title: "Reason", minLength: 3 },
        } },
      ],
    },
    basis: { reference: "R-7" }, context: { facts: [], references: [] },
    verdict: "PENDING", closed_reason: null, resolution: {}, resolved_at: null,
    expires_at: "2026-10-01T12:00:00Z", subject_model: "notes.Note", subject_id: "nte_7",
    requester: { display_name: "Requester" }, resolved_by: null,
    group: { id: "dcg_review" },
    ...overrides,
  };
}

export const decisionSubjectFixture = testDataResource("notes.Note", {
  capabilities: ["list", "detail"], recordRepresentation: "display_name",
  query: testResourceQuery({ fields: { id: testQueryField("id"), display_name: testQueryField("display_name") } }),
});

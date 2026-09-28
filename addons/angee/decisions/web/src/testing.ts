import type { Decision, DecisionSeat } from "./documents.console";
import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";

/** Executable resource metadata for inbox tests and interactive decision stories. */
export const decisionResourceFixture = testDataResource("decisions.Decision", {
  capabilities: ["list", "detail"],
  roots: { list: "decisions", detail: "decisions_by_pk", aggregate: "decisions_aggregate" },
  typeNames: { filter: "decisions_bool_exp", order: "decisions_order_by" },
  query: testResourceQuery({ fields: {
    ...Object.fromEntries([
      "id", "kind", "record_model_label", "record_public_id", "requester.display_name",
      "assignees", "requester", "verdict", "closed_reason", "expires_at",
    ].map((name) => [name, testQueryField(name)])),
    created_at: testQueryField("created_at", { sort: { field: "created_at" } }),
    is_open: testQueryField("is_open", { scalar: "Boolean", nullable: false,
      filter: { field: "is_open", scalar: "Boolean", values: [], operators: ["exact"] },
    }),
  } }),
});

/** Frozen neutral question shared by decision page, slot, and story fixtures. */
export function decisionFixture(overrides: Partial<Decision> = {}): Decision {
  return {
    id: "dcn_review", kind: "review", revision: 3, is_open: true, can_act: true,
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
    expires_at: "2026-10-01T12:00:00Z", record_model_label: "notes.Note", record_public_id: "nte_7",
    requester: { display_name: "Requester" }, resolved_by: null,
    group: { id: "dcg_review" },
    ...overrides,
  };
}

export function decisionSeatFixture(overrides: Partial<DecisionSeat> = {}): DecisionSeat {
  return { id: "dcn_review", index: 0, verdict: "PENDING", closed_reason: null,
    assignees: [{ display_name: "Reviewer" }], ...overrides };
}

export const decisionSubjectFixture = testDataResource("notes.Note", {
  capabilities: ["list", "detail"], recordRepresentation: "display_name",
  query: testResourceQuery({ fields: { id: testQueryField("id"), display_name: testQueryField("display_name") } }),
});

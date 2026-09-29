import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import type { DocumentType } from "@angee/gql/console";

import { WorkflowDocument, WORKFLOW_VERSION_MODEL } from "./documents.console";

export const workflowFixture: NonNullable<DocumentType<typeof WorkflowDocument>["workflow_by_pk"]> = {
  id: "wfl_review", key: "record_review", name: "Record review", description: "A retained review process.",
  subject_model: "notes.Note", published: { id: "wfv_review", number: 2, created_at: "2026-09-29T09:00:00Z" },
};

export const workflowVersionFixture = testDataResource(WORKFLOW_VERSION_MODEL, {
  capabilities: ["list", "detail"],
  roots: { list: "workflowversion", detail: "workflowversion_by_pk", aggregate: "workflowversion_aggregate" },
  typeNames: { filter: "workflowversion_bool_exp", order: "workflowversion_order_by" },
  query: testResourceQuery({ fields: {
    ...Object.fromEntries(["id", "workflow", "created_at", "published_by", "content_hash"].map((name) => [name, testQueryField(name)])),
    number: testQueryField("number", { scalar: "Int", sort: { field: "number" } }),
  } }),
});

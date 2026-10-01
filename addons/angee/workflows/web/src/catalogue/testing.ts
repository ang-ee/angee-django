import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import type { Workflow } from "../testing/documents.console";

import { WORKFLOW_VERSION_MODEL } from "./resources";
import { retainedField } from "../testing";

export const workflowFixture: Workflow = {
  id: "wfl_review", key: "record_review", name: "Record review", description: "A retained review process.",
  subject_model: "notes.Note", published: { id: "wfv_review", number: 2, created_at: "2026-09-29T09:00:00Z" },
};

export const workflowVersionFixture = testDataResource(WORKFLOW_VERSION_MODEL, {
  capabilities: ["list", "detail"],
  fields: [{ name: "workflow", kind: "relation", relationObject: true, relationModelLabel: "workflows.Workflow",
    readable: true, creatable: false, updatable: false, requiredOnCreate: false, aggregatable: false },
  retainedField("number", "Int"), ...["id", "created_at", "published_by", "content_hash"].map((name) => retainedField(name))],
  roots: { list: "workflowversion", detail: "workflowversion_by_pk", aggregate: "workflowversion_aggregate" },
  typeNames: { filter: "workflowversion_bool_exp", order: "workflowversion_order_by" },
  query: testResourceQuery({ fields: {
    ...Object.fromEntries(["id", "workflow", "created_at", "published_by", "content_hash"].map((name) => [name, testQueryField(name)])),
    number: testQueryField("number", { scalar: "Int", sort: { field: "number" } }),
    workflow: testQueryField("workflow", { kind: "relation",
      relation: { model: "workflows.Workflow", identityPath: "workflow.id", labelPath: "workflow.name" },
      row: { path: "workflow.id", paths: ["workflow.id", "workflow.name"] } }),
  } }),
});

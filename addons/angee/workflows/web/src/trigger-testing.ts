import { testDataResource, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import { retainedField } from "./testing";
import { TRIGGER_EVENT_MODEL, TRIGGER_MODEL } from "./triggers";

const sources = [{ value: "RECORD_CHANGED", description: "Record changed" }, { value: "CUSTOM_CHANGED", description: "Custom changed" }];
const relation = (name: string, model: string, writable = false) => ({
  ...retainedField(name), kind: "relation" as const, relationObject: true, relationModelLabel: model,
  creatable: writable, updatable: writable,
});
const writable = (name: string, scalar = "String") => ({ ...retainedField(name, scalar), creatable: true, updatable: true });

export const triggerResourceFixture = testDataResource(TRIGGER_MODEL, {
  recordRepresentation: "display_name",
  roots: { list: "trigger", detail: "trigger_by_pk", aggregate: "trigger_aggregate", create: "insert_trigger_one", update: "update_trigger_by_pk", delete: "delete_trigger_by_pk" },
  typeNames: { node: "TriggerType", filter: "trigger_bool_exp", order: "trigger_order_by", insert: "trigger_insert_input", update: "trigger_set_input", pkColumns: "trigger_pk_columns_input" },
  fields: [retainedField("id"), retainedField("display_name"), retainedField("enabled", "Boolean"), retainedField("can_edit", "Boolean"), retainedField("source_model"), retainedField("disabled_reason"),
    relation("run_as", "iam.User"), relation("workflow", "workflows.Workflow", true),
    { ...writable("source"), kind: "enum", values: sources }, writable("model_label"), writable("condition", "JSON")],
  query: testResourceQuery({ fields: {
    ...Object.fromEntries(["id", "display_name", "model_label", "source_model", "condition", "enabled", "can_edit", "disabled_reason", "run_as.id", "run_as.display_name", "workflow.id", "workflow.name"].map((name) => [name, testQueryField(name)])),
    source: testQueryField("source", { kind: "enum", values: sources }),
    run_as: testQueryField("run_as", { kind: "relation", scalar: "ID", relation: { model: "iam.User", identityPath: "run_as.id", labelPath: "run_as.display_name" }, row: { path: "run_as.id", paths: ["run_as.id", "run_as.display_name"] } }),
    workflow: testQueryField("workflow", { kind: "relation", scalar: "ID",
      relation: { model: "workflows.Workflow", identityPath: "workflow.id", labelPath: "workflow.name" },
      row: { path: "workflow.id", paths: ["workflow.id", "workflow.name"] } }),
  } }),
});

export const triggerEventResourceFixture = testDataResource(TRIGGER_EVENT_MODEL, {
  capabilities: ["list", "detail"], recordRepresentation: "display_name",
  roots: { list: "triggerevent", detail: "triggerevent_by_pk", aggregate: "triggerevent_aggregate" },
  typeNames: { node: "TriggerEventType", filter: "triggerevent_bool_exp", order: "triggerevent_order_by" },
  fields: [...["id", "display_name", "record_model", "record_id", "changed_at", "evaluated_at", "admitted_at", "rejection"].map((name) => retainedField(name)),
    relation("trigger", TRIGGER_MODEL), relation("run", "workflows.WorkflowRun")],
  query: testResourceQuery({ fields: {
    ...Object.fromEntries(["id", "display_name", "record_model", "record_id", "evaluated_at", "admitted_at", "rejection", "trigger.id", "run.id"].map((name) => [name, testQueryField(name)])),
    changed_at: testQueryField("changed_at", { sort: { field: "changed_at" } }),
    run: testQueryField("run", { kind: "relation", scalar: "ID", relation: { model: "workflows.WorkflowRun", identityPath: "run.id", labelPath: "run.id" }, row: { path: "run.id", paths: ["run.id"] } }),
    trigger: testQueryField("trigger", { kind: "relation", scalar: "ID", relation: { model: TRIGGER_MODEL, identityPath: "trigger.id", labelPath: "trigger.id" }, row: { path: "trigger.id", paths: ["trigger.id"] } }),
  } }),
});

export const triggerFixture = { id: "wft_review", display_name: "Review admission", workflow: { id: "wfl_review", name: "Record review" },
  source: "RECORD_CHANGED", model_label: "notes.Note", source_model: "notes.Note", condition: {}, enabled: false, can_edit: true, run_as: null, disabled_reason: "" };
export const triggerEventFixture = { id: "wte_review", display_name: "Review event", trigger: { id: "wft_review" },
  record_model: "notes.Note", record_id: "nte_7", changed_at: "2026-09-29T09:00:00Z", evaluated_at: "2026-09-29T09:00:01Z",
  admitted_at: "2026-09-29T09:00:01Z", rejection: "", run: { id: "wfr_review" } };

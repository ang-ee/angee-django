import { testDataResource, testQueryAxis, testQueryField, testResourceQuery } from "@angee/metadata/testing";

import type { Run, StepRun } from "./documents.console";

const runStates = [
  { value: "RUNNING", description: "Running" }, { value: "WAITING", description: "Waiting" },
  { value: "SUCCEEDED", description: "Succeeded" }, { value: "FAILED", description: "Failed" },
  { value: "CANCELED", description: "Canceled" },
];
const origins = [
  { value: "MANUAL", description: "Manual" }, { value: "WORKFLOW", description: "Workflow" },
  { value: "REPROCESS", description: "Reprocess" }, { value: "TEST", description: "Test" },
];
const statusValues = runStates.map(({ value }) => ({ from: value, to: value.toLowerCase() }));

/** Executable metadata shared by the run stories and provider tests. */
export const runResourceFixture = testDataResource("workflows.WorkflowRun", {
  capabilities: ["list", "detail"],
  roots: { list: "workflowrun", detail: "workflowrun_by_pk", aggregate: "workflowrun_aggregate",
    groups: "workflowrun_groups", groupsCount: "workflowrun_groups_count" },
  typeNames: { filter: "workflowrun_bool_exp", order: "workflowrun_order_by",
    groupBySpec: "workflowrunGroupBySpec", groupOrder: "workflowrunGroupOrder", having: "workflowrunHaving" },
  fields: [...["id", "subject_id", "subject_model", "created_at", "finished_at", "outcome"].map((name) => ({
    name, kind: "scalar" as const, scalar: "String", readable: true, aggregatable: false,
    creatable: false, updatable: false, requiredOnCreate: false,
  })), { name: "version", kind: "relation", relationObject: true, relationModelLabel: "workflows.WorkflowVersion",
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false },
  { name: "status", kind: "enum", values: runStates, readable: true,
    filterable: true, sortable: true, aggregatable: false, groupable: true,
    creatable: false, updatable: false, requiredOnCreate: false },
  { name: "origin", kind: "enum", values: origins, readable: true, aggregatable: false,
    creatable: false, updatable: false, requiredOnCreate: false },
  { name: "workflow", kind: "relation", relationObject: true, relationModelLabel: "workflows.Workflow",
    readable: true, filterable: true, groupable: true, aggregatable: false,
    creatable: false, updatable: false, requiredOnCreate: false }],
  query: testResourceQuery({ fields: {
    ...Object.fromEntries([
      "id", "origin", "outcome", "subject_model", "subject_id",
      "version.id", "version.number", "version.workflow.id", "version.workflow.name",
      "version.workflow.key",
    ].map((name) => [name, testQueryField(name)])),
    status: testQueryField("status", { kind: "enum", values: runStates,
      filter: { field: "status", scalar: "String", values: runStates, valueMap: statusValues, operators: ["exact", "inList"] } }),
    workflow: testQueryField("workflow", { kind: "relation", scalar: "ID",
      relation: { model: "workflows.Workflow", identityPath: "version.workflow.id", labelPath: "version.workflow.name" },
      row: { path: "version.workflow.id", paths: ["version.workflow.id", "version.workflow.name"] },
      filter: { field: "workflow", scalar: "ID", values: [], operators: ["exact", "inList", "isNull"] } }),
    origin: testQueryField("origin", { kind: "enum", values: origins }),
    created_at: testQueryField("created_at", { sort: { field: "created_at" } }),
    finished_at: testQueryField("finished_at", { sort: { field: "finished_at" } }),
  }, axes: {
    status: testQueryAxis("status", { server: { input: "STATUS", key: "status" },
      drill: { kind: "value", field: "status", valueKey: "status", nullMode: "isNull", valueMap: statusValues } }),
    workflow: testQueryAxis("workflow", { kind: "relation", identityPath: "version.workflow.id",
      labelPath: "version.workflow.name", paths: ["version.workflow.id", "version.workflow.name"],
      server: { input: "WORKFLOW", key: "workflow_id", labelInput: "WORKFLOW__NAME", labelKey: "workflow__name" },
      drill: { kind: "identity", field: "workflow", valueKey: "workflow_id", nullMode: "isNull", valueMap: [] } }),
  } }),
});

export const stepRunResourceFixture = testDataResource("workflows.StepRun", {
  capabilities: ["list", "detail"],
  roots: { list: "steprun", detail: "steprun_by_pk", aggregate: "steprun_aggregate" },
  typeNames: { filter: "steprun_bool_exp", order: "steprun_order_by" },
  query: testResourceQuery({ fields: {
    ...Object.fromEntries(["id", "run", "node_key"].map((name) => [name, testQueryField(name)])),
    rank: testQueryField("rank", { scalar: "Int", sort: { field: "rank" } }),
    map_index: testQueryField("map_index", { scalar: "Int", sort: { field: "map_index" } }),
    is_mapped: testQueryField("is_mapped", { scalar: "Boolean" }),
  } }),
});

export const runSubjectFixture = testDataResource("notes.Note", {
  capabilities: ["list", "detail"], recordRepresentation: "display_name",
  roots: { list: "notes", detail: "notes_by_pk" },
  query: testResourceQuery({ fields: {
    id: testQueryField("id"), display_name: testQueryField("display_name"),
  } }),
});

export const workflowResourceFixture = testDataResource("workflows.Workflow", {
  capabilities: ["list", "detail"], recordRepresentation: "name",
  roots: { list: "workflow", detail: "workflow_by_pk", aggregate: "workflow_aggregate" },
  typeNames: { filter: "workflow_bool_exp", order: "workflow_order_by" },
  query: testResourceQuery({ fields: {
    ...Object.fromEntries(["id", "key", "name", "subject_model", "published.number"]
      .map((name) => [name, testQueryField(name)])),
  } }),
});

/** A retained L2 run; types come from the authored operation, never a parallel shape. */
export function runFixture(overrides: Partial<Run> = {}): Run {
  return {
    id: "wfr_review", status: "FAILED", origin: "MANUAL", subject_model: "notes.Note", subject_id: "nte_7",
    can_cancel: false, can_reprocess: true,
    run_as: { id: "usr_operator", display_name: "Operator" }, input: { reference: "R-7" }, output: {}, outcome: "", error: "",
    created_at: "2026-09-29T09:00:00Z", finished_at: "2026-09-29T09:01:00Z", reprocess_of: null,
    version: { id: "wfv_review", number: 2,
      workflow: { id: "wfl_review", key: "record_review", name: "Record review", subject_model: "notes.Note" } },
    ...overrides,
  };
}

export function stepRunFixture(overrides: Partial<StepRun> = {}): StepRun {
  return {
    id: "wsr_inspect", node_key: "inspect", map_index: 0, is_mapped: false, rank: 0,
    can_retry: true, requires_duplicate_acknowledgement: false,
    status: "FAILED", outcome: "error", attempt: 1, waiting_kind: null, wait_reason: "",
    input: { reference: "R-7" }, output: {},
    attempts: [{ id: "wsa_inspect", number: 1, result: "TIMED_OUT", started_at: "2026-09-29T09:00:00Z",
      finished_at: "2026-09-29T09:01:00Z", error: "The operation did not finish.", stacktrace: "TimeoutError: operation expired" }],
    artifacts: [{ id: "wfa_note", label: "Retained note", model_label: "notes.Note", record_id: "nte_7" }],
    ...overrides,
  };
}

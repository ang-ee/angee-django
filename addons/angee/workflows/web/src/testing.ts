import { testDataResource, testQueryAxis, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import type { DataResourceFieldMetadata } from "@angee/metadata";
import { decisionResourceFixture } from "@angee/decisions/testing";
import { titleCase } from "@angee/ui";

import type { Run, StepRun } from "./testing/documents.console";
import type { RunGraphData } from "./run-graph";

const runStates = [
  { value: "RUNNING", description: "Running" }, { value: "WAITING", description: "Waiting" },
  { value: "SUCCEEDED", description: "Succeeded" }, { value: "FAILED", description: "Failed" },
  { value: "CANCELED", description: "Canceled" },
];
const origins = [
  { value: "MANUAL", description: "Manual" }, { value: "WORKFLOW", description: "Workflow" },
  { value: "REPROCESS", description: "Reprocess" },
  { value: "TRIGGER", description: "Trigger" },
];
const stepStates = [{ value: "READY", description: "Ready" }, ...runStates, { value: "SKIPPED", description: "Skipped" }];
const waitKinds = ["TIME", "RECORD", "DECISION", "MAP", "RUN", "OPERATOR"]
  .map((value) => ({ value, description: titleCase(value.toLowerCase()) }));
const statusValues = runStates.map(({ value }) => ({ from: value, to: value.toLowerCase() }));

export function retainedField(name: string, scalar = "String"): DataResourceFieldMetadata {
  return { name, kind: "scalar", scalar, readable: true, aggregatable: false,
    creatable: false, updatable: false, requiredOnCreate: false };
}

/** Executable metadata shared by the run stories and provider tests. */
export const runResourceFixture = testDataResource("workflows.WorkflowRun", {
  capabilities: ["list", "detail"],
  roots: { list: "workflowrun", detail: "workflowrun_by_pk", aggregate: "workflowrun_aggregate",
    groups: "workflowrun_groups", groupsCount: "workflowrun_groups_count" },
  typeNames: { filter: "workflowrun_bool_exp", order: "workflowrun_order_by",
    groupBySpec: "workflowrunGroupBySpec", groupOrder: "workflowrunGroupOrder", having: "workflowrunHaving" },
  recordRepresentation: "display_name",
  fields: [...["id", "display_name", "subject_id", "subject_model", "created_at", "finished_at", "outcome", "outcome_label"].map((name) => ({
    name, kind: "scalar" as const, scalar: "String", readable: true, aggregatable: false,
    creatable: false, updatable: false, requiredOnCreate: false,
  })), { name: "version", kind: "relation", relationObject: true, relationModelLabel: "workflows.WorkflowVersion",
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false },
  { name: "status", kind: "enum", values: runStates, readable: true,
    filterable: true, sortable: true, aggregatable: false, groupable: true,
    creatable: false, updatable: false, requiredOnCreate: false },
  { name: "origin", kind: "enum", values: origins, readable: true, aggregatable: false,
    filterable: true, groupable: true,
    creatable: false, updatable: false, requiredOnCreate: false },
  ...["error", "failure_reason", "can_cancel", "can_reprocess", "input", "output"].map((name) => ({
    name, kind: "scalar" as const, scalar: name.startsWith("can_") ? "Boolean" : name === "input" || name === "output" ? "JSON" : "String",
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
  })),
  ...[["run_as", "iam.User"], ["reprocess_of", "workflows.WorkflowRun"], ["parent_step", "workflows.StepRun"], ["trigger_event", "workflows.TriggerEvent"]].map(([name, relationModelLabel]) => ({
    name: name!, relationModelLabel: relationModelLabel!, kind: "relation" as const, relationObject: true,
    readable: true, aggregatable: false, creatable: false, updatable: false, requiredOnCreate: false,
  }))],
  query: testResourceQuery({ fields: {
    ...Object.fromEntries([
      "id", "display_name", "origin", "outcome", "outcome_label", "subject_model", "subject_id", "error", "failure_reason", "input", "output", "can_cancel", "can_reprocess",
      "run_as.id", "run_as.display_name", "reprocess_of.id",
      "version.id", "version.number", "version.workflow.id", "version.workflow.name",
      "version.workflow.key", "parent_step.id", "parent_step.run.id", "trigger_event.id",
    ].map((name) => [name, testQueryField(name)])),
    status: testQueryField("status", { kind: "enum", values: runStates,
      filter: { field: "status", scalar: "String", values: [], valueMap: statusValues, operators: ["exact", "inList"] } }),
    "version.workflow": testQueryField("version.workflow", { kind: "relation", scalar: "ID",
      relation: { model: "workflows.Workflow", identityPath: "version.workflow.id", labelPath: "version.workflow.name" },
      row: { path: "version.workflow.id", paths: ["version.workflow.id", "version.workflow.name"] },
      filter: { field: "version__workflow", scalar: "ID", values: [], operators: ["exact", "inList", "isNull"] } }),
    "parent_step.run": testQueryField("parent_step.run", { kind: "relation", scalar: "ID",
      relation: { model: "workflows.WorkflowRun", identityPath: "parent_step.run.id", labelPath: "parent_step.run.id" },
      row: { path: "parent_step.run.id", paths: ["parent_step.run.id"] },
      filter: { field: "parent_step__run", scalar: "ID", values: [], operators: ["exact", "inList", "isNull"] } }),
    origin: testQueryField("origin", { kind: "enum", values: origins,
      filter: { field: "origin", scalar: "String", values: [],
        valueMap: origins.map(({ value }) => ({ from: value, to: value.toLowerCase() })), operators: ["exact", "inList"] } }),
    created_at: testQueryField("created_at", { sort: { field: "created_at" } }),
    finished_at: testQueryField("finished_at", { sort: { field: "finished_at" } }),
  }, axes: {
    origin: testQueryAxis("origin", { server: { input: "ORIGIN", key: "origin" },
      drill: { kind: "value", field: "origin", valueKey: "origin", nullMode: "isNull", valueMap: origins.map(({ value }) => ({ from: value, to: value.toLowerCase() })) } }),
    status: testQueryAxis("status", { server: { input: "STATUS", key: "status" },
      drill: { kind: "value", field: "status", valueKey: "status", nullMode: "isNull", valueMap: statusValues } }),
    "version.workflow": testQueryAxis("version.workflow", { kind: "relation", identityPath: "version.workflow.id",
      labelPath: "version.workflow.name", paths: ["version.workflow.id", "version.workflow.name"],
      server: { input: "VERSION__WORKFLOW", key: "version__workflow_id",
        labelInput: "VERSION__WORKFLOW__NAME", labelKey: "version__workflow__name" },
      drill: { kind: "identity", field: "version.workflow", valueKey: "version__workflow_id", nullMode: "isNull", valueMap: [] } }),
  } }),
});

export const runEvidenceResourceFixture = testDataResource("workflows.WorkflowRunEvidence", {
  capabilities: ["list", "detail"],
  roots: { list: "workflowrunevidence", detail: "workflowrunevidence_by_pk", aggregate: "workflowrunevidence_aggregate" },
  typeNames: { filter: "workflowrunevidence_bool_exp", order: "workflowrunevidence_order_by" },
  fields: ["id", "record_model", "record_id"].map((name) => retainedField(name)),
  query: testResourceQuery({ fields: {
    id: testQueryField("id"), record_model: testQueryField("record_model"), record_id: testQueryField("record_id"),
    run: testQueryField("run", { kind: "relation", scalar: "ID",
      filter: { field: "run", scalar: "ID", values: [], operators: ["exact"] } }),
  } }),
});

export const stepRunResourceFixture = testDataResource("workflows.StepRun", {
  capabilities: ["list", "detail"],
  recordRepresentation: "node_label",
  fields: [
    ...["id", "node_key", "node_label", "outcome", "outcome_label", "wait_reason", "failure_reason",
      "deadline_at", "wake_at", "created_at", "updated_at"].map((name) => retainedField(name)),
    ...["run", "awaited_run"].map((name) => ({ ...retainedField(name), kind: "relation" as const,
      relationObject: true, relationModelLabel: "workflows.WorkflowRun" })),
    ...["rank", "map_index", "map_settled", "map_total", "attempt", "page_index", "retries"].map((name) => retainedField(name, "Int")),
    ...["can_retry", "requires_duplicate_acknowledgement", "is_mapped", "is_map"].map((name) => retainedField(name, "Boolean")),
    ...["input", "output", "state"].map((name) => retainedField(name, "JSON")),
    { ...retainedField("status"), kind: "enum", values: stepStates },
    { ...retainedField("waiting_kind"), kind: "enum", values: waitKinds },
  ],
  roots: { list: "steprun", detail: "steprun_by_pk", aggregate: "steprun_aggregate" },
  typeNames: { filter: "steprun_bool_exp", order: "steprun_order_by" },
  query: testResourceQuery({ fields: {
    ...Object.fromEntries(["id", "node_key", "node_label", "outcome", "outcome_label", "attempt", "wait_reason", "input", "output", "can_retry", "requires_duplicate_acknowledgement"].map((name) => [name, testQueryField(name)])),
    ...Object.fromEntries(["run", "awaited_run"].map((name) => [name, testQueryField(name, {
      kind: "relation", scalar: "ID",
      relation: { model: "workflows.WorkflowRun", identityPath: `${name}.id`, labelPath: `${name}.id` },
      row: { path: `${name}.id`, paths: [`${name}.id`] },
    })])),
    ...Object.fromEntries(["state", "page_index", "failure_reason", "retries", "deadline_at", "wake_at", "created_at", "updated_at"]
      .map((name) => [name, testQueryField(name)])),
    status: testQueryField("status", { kind: "enum", values: stepStates,
      filter: { field: "status", scalar: "String", values: stepStates,
        valueMap: stepStates.map(({ value }) => ({ from: value, to: value.toLowerCase() })), operators: ["exact", "inList"] } }),
    waiting_kind: testQueryField("waiting_kind", { kind: "enum", values: waitKinds }),
    node_key: testQueryField("node_key", {
      filter: { field: "node_key", scalar: "String", values: [], operators: ["exact", "inList"] } }),
    rank: testQueryField("rank", { scalar: "Int", sort: { field: "rank" } }),
    map_index: testQueryField("map_index", { scalar: "Int", sort: { field: "map_index" } }),
    is_mapped: testQueryField("is_mapped", { scalar: "Boolean" }),
    is_map: testQueryField("is_map", { scalar: "Boolean" }),
    map_settled: testQueryField("map_settled", { scalar: "Int" }),
    map_total: testQueryField("map_total", { scalar: "Int" }),
    "run.id": testQueryField("run.id"),
    "awaited_run.id": testQueryField("awaited_run.id"),
  } }),
});

export const attemptResourceFixture = testDataResource("workflows.StepAttempt", {
  recordRepresentation: "number",
  fields: [retainedField("number", "Int"), retainedField("page_index", "Int"), ...["id", "step_run", "result", "started_at", "finished_at", "error", "stacktrace"].map((name) => retainedField(name))],
  capabilities: ["list", "detail"], roots: { list: "stepattempt", detail: "stepattempt_by_pk", aggregate: "stepattempt_aggregate" },
  typeNames: { filter: "stepattempt_bool_exp", order: "stepattempt_order_by" },
  query: testResourceQuery({ fields: Object.fromEntries(
    ["id", "step_run", "number", "page_index", "result", "started_at", "finished_at", "error", "stacktrace"]
      .map((name) => [name, testQueryField(name, { sort: { field: name } })]),
  ) }),
});
export const artifactResourceFixture = testDataResource("workflows.StepArtifact", {
  recordRepresentation: "label",
  fields: ["id", "step_run", "label", "record_model", "record_id"].map((name) => retainedField(name)),
  capabilities: ["list", "detail"], roots: { list: "stepartifact", detail: "stepartifact_by_pk", aggregate: "stepartifact_aggregate" },
  typeNames: { filter: "stepartifact_bool_exp", order: "stepartifact_order_by" },
  query: testResourceQuery({ fields: Object.fromEntries(
    ["id", "step_run", "label", "record_model", "record_id"].map((name) => [name, testQueryField(name)]),
  ) }),
});
export const userResourceFixture = testDataResource("iam.User", {
  fields: ["id", "display_name"].map((name) => retainedField(name)),
  capabilities: ["detail"], roots: { detail: "user_by_pk" }, recordRepresentation: "display_name",
  query: testResourceQuery({ fields: { id: testQueryField("id"), display_name: testQueryField("display_name") } }),
});

export const watchResourceFixture = testDataResource("workflows.StepWatch", {
  fields: ["id", "step_run", "record_model", "record_id"].map((name) => retainedField(name)),
  capabilities: ["list", "detail"], roots: { list: "stepwatch", detail: "stepwatch_by_pk", aggregate: "stepwatch_aggregate" },
  typeNames: { filter: "stepwatch_bool_exp", order: "stepwatch_order_by" },
  query: testResourceQuery({ fields: Object.fromEntries(
    ["id", "step_run", "record_model", "record_id"].map((name) => [name, testQueryField(name)]),
  ) }),
});

/** The workflows-owned decision filter extends the decisions fixture's query contract. */
export const stepDecisionResourceFixture = testDataResource("decisions.Decision", {
  ...decisionResourceFixture,
  query: testResourceQuery({ ...decisionResourceFixture.query, fields: {
    ...decisionResourceFixture.query.fields,
    "step_run": testQueryField("step_run", { kind: "relation", scalar: "ID",
      filter: { field: "step_run", scalar: "ID", values: [], operators: ["exact"] } }),
  } }),
});

export const runSubjectFixture = testDataResource("notes.Note", {
  fields: ["id", "display_name"].map((name) => retainedField(name)),
  capabilities: ["list", "detail"], recordRepresentation: "display_name",
  roots: { list: "notes", detail: "notes_by_pk" },
  query: testResourceQuery({ fields: {
    id: testQueryField("id"), display_name: testQueryField("display_name"),
  } }),
});

export const workflowResourceFixture = testDataResource("workflows.Workflow", {
  fields: [...["id", "key", "name", "description", "subject_model", "permissions"].map((name) => retainedField(name)),
    { ...retainedField("published"), kind: "relation", relationObject: true, relationModelLabel: "workflows.WorkflowVersion" }],
  capabilities: ["list", "detail"], recordRepresentation: "name",
  roots: { list: "workflow", detail: "workflow_by_pk", aggregate: "workflow_aggregate" },
  typeNames: { filter: "workflow_bool_exp", order: "workflow_order_by" },
  query: testResourceQuery({ fields: {
    ...Object.fromEntries(["id", "key", "name", "description", "subject_model", "published.number", "permissions"]
      .map((name) => [name, testQueryField(name)])),
    name: testQueryField("name", { sort: { field: "name" } }),
  } }),
});

/** A retained L2 run typed by its generated fixture fragment. */
export function runFixture(overrides: Partial<Run> = {}): Run {
  return {
    id: "wfr_review", display_name: "Record review run", status: "FAILED", origin: "MANUAL", subject_model: "notes.Note", subject_id: "nte_7",
    can_cancel: false, can_reprocess: true,
    run_as: { id: "usr_operator", display_name: "Operator" }, input: { reference: "R-7" }, output: {}, outcome: "", outcome_label: "", error: "", failure_reason: null,
    created_at: "2026-09-29T09:00:00Z", finished_at: "2026-09-29T09:01:00Z", reprocess_of: null, parent_step: null, trigger_event: null,
    version: { id: "wfv_review", number: 2,
      workflow: { id: "wfl_review", key: "record_review", name: "Record review", subject_model: "notes.Note" } },
    ...overrides,
  };
}

export function stepRunFixture(overrides: Partial<StepRun> = {}): StepRun {
  return {
    id: "wsr_inspect", node_key: "inspect", node_label: overrides.node_key ?? "Inspect source", map_index: 0, is_mapped: false, is_map: false, map_settled: 0, map_total: 0, rank: 0,
    can_retry: true, requires_duplicate_acknowledgement: false,
    status: "FAILED", outcome: "error", outcome_label: "Needs attention", attempt: 1, waiting_kind: null, wait_reason: "", awaited_run: null,
    input: { reference: "R-7" }, output: {},
    state: { cursor: "page-2" }, page_index: 1, failure_reason: "The operation did not finish.", retries: 0,
    deadline_at: null, wake_at: null, created_at: "2026-09-29T09:00:00Z", updated_at: "2026-09-29T09:01:00Z",
    attempts: [{ id: "wsa_inspect", number: 1, page_index: 1, result: "TIMED_OUT", started_at: "2026-09-29T09:00:00Z",
      finished_at: "2026-09-29T09:01:00Z", error: "The operation did not finish.", stacktrace: "TimeoutError: operation expired" }],
    artifacts: [{ id: "wfa_note", label: "Retained note", record_model: "notes.Note", record_id: "nte_7" }],
    watches: [], decisions: [],
    ...overrides,
  };
}

/** Authored topology and summary fields, independent of definition JSON and list paging. */
export function runGraphFixture(step: StepRun = stepRunFixture()): RunGraphData {
  const { id, status, waiting_kind, wait_reason, outcome, outcome_label, failure_reason, attempt,
    page_index, map_total, map_settled, created_at, updated_at, deadline_at, wake_at } = step;
  return { nodes: [
    { key: "inspect", label: "Inspect source", step_label: "Inspect", rank: 0, body_key: null,
      outcomes: [{ outcome: "done", label: "Done" }, { outcome: "error", label: "Needs attention" }],
      item_counts: [], item_attempts: 0,
      step_run: { id, status, waiting_kind, wait_reason, outcome, outcome_label, failure_reason, attempt,
        page_index, map_total, map_settled, created_at, updated_at, deadline_at, wake_at } },
    { key: "finish", label: "Finish review", step_label: "Finish", rank: 1, body_key: null,
      outcomes: [], item_counts: [], item_attempts: 0, step_run: null },
  ], edges: [{ source: "inspect", outcome: "done", target: "finish", taken: false }] };
}

export function mappedRunGraphFixture(): RunGraphData {
  const graph = runGraphFixture(stepRunFixture({ id: "wsr_reviews", status: "WAITING", waiting_kind: "MAP",
    failure_reason: null, outcome: "", outcome_label: "", wait_reason: "", map_settled: 119, map_total: 122,
    page_index: 0, attempt: 1 }));
  graph.nodes[0] = { ...graph.nodes[0]!, key: "reviews", label: "Review items", step_label: "Map", body_key: "reviews.body",
    item_counts: [{ status: "SUCCEEDED", count: 118 }, { status: "RUNNING", count: 3 }, { status: "FAILED", count: 1 }],
    item_attempts: 141 };
  graph.edges[0] = { source: "reviews", outcome: "done", target: "finish", taken: false };
  return graph;
}

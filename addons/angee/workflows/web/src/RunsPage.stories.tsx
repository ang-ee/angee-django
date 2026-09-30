import { useMemo } from "react";
import * as v from "valibot";
import { operationDocuments } from "@angee/gql/console/actions";
import { RoutedRuntimeFixture, jsonResponse, storySchema } from "@angee/storybook/testing";
import { createRouteHref, JsonValueSchema } from "@angee/ui";

import { RunsPage } from "./RunsPage";
import type { Run, StepRun } from "./testing/documents.console";
import { runFixture, runResourceFixture, runEvidenceResourceFixture, runSubjectFixture, stepRunFixture, stepRunResourceFixture, workflowResourceFixture, attemptResourceFixture, artifactResourceFixture, userResourceFixture, watchResourceFixture } from "./testing";
import { workflowVersionFixture } from "./catalogue/testing";
import { triggerEventResourceFixture } from "./trigger-testing";

export default { title: "Workflows/Run page", parameters: { layout: "fullscreen" }, excludeStories: ["RunStory"] };
export const Recovery = { render: () => <RunStory /> };
export const Runs = { render: () => <RunStory list /> };
export const Waiting = { render: () => <RunStory waiting /> };
export const Watching = { render: () => <RunStory
  run={runFixture({ status: "WAITING", can_cancel: true, can_reprocess: false, finished_at: null })}
  steps={[stepRunFixture({ status: "WAITING", outcome: "", waiting_kind: "RECORD", can_retry: false,
    watches: [{ id: "wsw_note", record_model: "notes.Note", record_id: "nte_7" }] })]} /> };
export const DuplicateRisk = { render: () => <RunStory steps={[stepRunFixture({ requires_duplicate_acknowledgement: true })]} /> };
export const ReadOnly = { render: () => <RunStory run={runFixture({ can_reprocess: false })} steps={[stepRunFixture({ can_retry: false })]} /> };
export const Mapped = { render: () => <RunStory steps={mappedSteps} /> };
export const Redacted = { render: () => <RunStory redacted /> };
export const Unavailable = { render: () => <RunStory unavailable /> };
export const QueryError = { render: () => <RunStory queryError /> };
export const ActionRejected = { render: () => <RunStory rejectAction /> };

const RequestSchema = v.object({ query: v.string(), variables: v.optional(v.record(v.string(), JsonValueSchema), {}) });
export type RunRequest = v.InferOutput<typeof RequestSchema>;
const mappedSteps = Array.from({ length: 11 }, (_, index) => stepRunFixture({
  id: `wsr_mapped_${index}`, node_key: "mapped.body", is_mapped: true, map_index: index, rank: 0,
  can_retry: false, attempts: [], artifacts: [],
}));
const documents = { console: operationDocuments };
const runtime = {
  routeHref: createRouteHref([
    { name: "workflows.runs", path: "/workflows/runs" },
    { name: "workflows.runs.record", path: "/workflows/runs/$id" },
    { name: "workflows.catalogue", path: "/workflows" },
    { name: "workflows.catalogue.record", path: "/workflows/$id" },
    { name: "notes", path: "/notes" },
    { name: "notes.record", path: "/notes/$id" },
    { name: "workflows.trigger-events.record", path: "/workflows/trigger-events/$id" },
  ]),
  routesByResource: {
    "workflows.WorkflowRun": { collection: "workflows.runs", record: { name: "workflows.runs.record", param: "id" } },
    "workflows.Workflow": { collection: "workflows.catalogue", record: { name: "workflows.catalogue.record", param: "id" } },
    "notes.Note": { collection: "notes", record: { name: "notes.record", param: "id" } },
    "workflows.TriggerEvent": { collection: "workflows.trigger-events", record: { name: "workflows.trigger-events.record", param: "id" } },
  },
  auth: { user: { id: "usr_operator", name: "Operator" }, status: "authenticated" as const, hasRole: () => false },
};

/** Real router, query transport and generated mutation documents over retained fixture rows. */
export function RunStory({ list = false, waiting = false, redacted = false, unavailable = false, queryError = false, rejectAction = false,
  run, steps, children, evidence = [], onRequest }: {
  list?: boolean; waiting?: boolean; redacted?: boolean; unavailable?: boolean; queryError?: boolean; rejectAction?: boolean;
  run?: Run; steps?: readonly StepRun[]; children?: readonly Run[];
  evidence?: readonly { id: string; record_model: string | null; record_id: string | null }[];
  onRequest?: (request: RunRequest) => void;
}) {
  const schemas = useMemo(() => {
    let current = run ?? runFixture();
    let currentSteps = [...(steps ?? [stepRunFixture()])];
    if (waiting) {
      current = { ...current, status: "WAITING", can_cancel: true, can_reprocess: false, finished_at: null };
      currentSteps = currentSteps.map((step) => ({
      ...step, status: "WAITING", waiting_kind: "OPERATOR", wait_reason: "Dispatch attempts exhausted.",
      }));
    }
    if (redacted) currentSteps = currentSteps.map((step) => ({
      ...step, attempts: step.attempts.map((attempt) => ({ ...attempt, error: "", stacktrace: "" })),
    }));
    const fixture = storySchema(async (_input, init) => {
      const request = v.parse(RequestSchema, JSON.parse(String(init?.body ?? "{}")));
      onRequest?.(request);
      const { query, variables } = request;
      const action = ["cancel_workflow_run", "reprocess_workflow_run", "retry_step_accepting_duplicate", "retry_step"]
        .find((name) => query.includes(`${name}(`));
      if (action) {
        if (rejectAction) return jsonResponse({ data: { [action]: { ok: false, message: "This step cannot be retried in place.", validation_errors: {} } } });
        const messages: Record<string, string> = {
          cancel_workflow_run: "Open work canceled; the retained run is unchanged.",
          reprocess_workflow_run: "Run reprocessed.",
          retry_step: "Step retried; the run is waiting.",
          retry_step_accepting_duplicate: "Step retried with duplicate risk acknowledged.",
        };
        if (action.startsWith("retry_step")) {
          current = { ...current, status: "WAITING", can_cancel: true, can_reprocess: false, finished_at: null };
          currentSteps = currentSteps.map((step) => step.id === variables.id
            ? { ...step, status: "READY", can_retry: false, waiting_kind: null, wait_reason: "" } : step);
        }
        return jsonResponse({ data: { [action]: { ok: true, message: messages[action], id: action === "reprocess_workflow_run" ? "wfr_replacement" : String(variables.id) } } });
      }
      if (query.includes("workflowrun_by_pk")) return queryError
        ? jsonResponse({ errors: [{ message: "The run could not be loaded." }] })
        : jsonResponse({ data: { workflowrun_by_pk: unavailable ? null : { ...current, id: variables.id } } });
      if (query.includes("workflow_by_pk")) return jsonResponse({ data: { workflow_by_pk: current.version?.workflow } });
      if (query.includes("user_by_pk")) return jsonResponse({ data: { user_by_pk: current.run_as } });
      if (query.includes("triggerevent_by_pk")) return jsonResponse({ data: { triggerevent_by_pk: { id: "wte_review", display_name: "Review event" } } });
      if (query.includes("notes_by_pk")) return jsonResponse({ data: { notes_by_pk: { id: "nte_7", display_name: "Review notes" } } });
      if (query.includes("workflowrunevidence")) return jsonResponse({ data: {
        workflowrunevidence: evidence, workflowrunevidence_aggregate: { aggregate: { count: evidence.length } },
      } });
      if (query.includes("steprun_by_pk")) return jsonResponse({ data: { steprun_by_pk: currentSteps.find((step) => step.id === variables.id) ?? null } });
      if (query.includes("stepattempt_by_pk")) return jsonResponse({ data: { stepattempt_by_pk: currentSteps.flatMap((step) => step.attempts).find((attempt) => attempt.id === variables.id) } });
      for (const [name, rows] of [["stepattempt", currentSteps.flatMap((step) => step.attempts)], ["stepartifact", currentSteps.flatMap((step) => step.artifacts)], ["stepwatch", currentSteps.flatMap((step) => step.watches)]] as const) {
        if (query.includes(name)) return jsonResponse({ data: { [name]: rows, [`${name}_aggregate`]: { aggregate: { count: rows.length } } } });
      }
      if (/\bsteprun(?:\s*\(|\s*\{)/.test(query)) {
        const offset = typeof variables.offset === "number" ? variables.offset : 0;
        const limit = typeof variables.limit === "number" ? variables.limit : currentSteps.length;
        return jsonResponse({ data: { steprun: currentSteps.slice(offset, offset + limit), steprun_aggregate: { aggregate: { count: currentSteps.length } } } });
      }
      if (query.includes("steprun_aggregate")) return jsonResponse({ data: { steprun_aggregate: { aggregate: { count: currentSteps.length } } } });
      if (query.includes("workflowrun_groups")) return jsonResponse({ data: { workflowrun_groups: [{
        key: { status: current.status, origin: current.origin, version__workflow_id: current.version?.workflow?.id,
          version__workflow__name: current.version?.workflow?.name },
        aggregate: { count: 1 },
      }], totalCount: 1 } });
      if (/\bworkflow\s*\(/.test(query)) return jsonResponse({ data: { workflow: [current.version?.workflow], workflow_aggregate: { aggregate: { count: 1 } } } });
      const rows = JSON.stringify(variables.where ?? {}).includes("parent_step__run") ? children ?? [] : [current];
      return jsonResponse({ data: { workflowrun: rows, workflowrun_aggregate: { aggregate: { count: rows.length } } } });
    }).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [
      runResourceFixture, runEvidenceResourceFixture, stepRunResourceFixture, workflowResourceFixture, runSubjectFixture,
      workflowVersionFixture, attemptResourceFixture, artifactResourceFixture, userResourceFixture,
      triggerEventResourceFixture, watchResourceFixture,
    ] } } } };
  }, [waiting, redacted, unavailable, queryError, rejectAction, run, steps, children, evidence, onRequest]);
  return <RoutedRuntimeFixture activeSchema="console" schemas={schemas} collectionPath="/workflows/runs"
    initialEntry={list ? "/workflows/runs" : "/workflows/runs/wfr_review"} runtime={runtime}
    resourceName="workflows.WorkflowRun" resourceLabel="Runs" operationDocuments={documents}>
    <RunsPage />
  </RoutedRuntimeFixture>;
}

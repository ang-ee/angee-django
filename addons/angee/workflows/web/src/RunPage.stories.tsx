import { useMemo } from "react";
import * as v from "valibot";
import { operationDocuments } from "@angee/gql/console/actions";
import { RoutedRuntimeFixture, jsonResponse, storySchema, testDataResource } from "@angee/storybook/testing";
import { createRouteHref, JsonValueSchema, useRouteParam } from "@angee/ui";

import { RunPage } from "./RunPage";
import { RunsPage } from "./RunsPage";
import type { Run, StepRun } from "./documents.console";
import { runFixture, runResourceFixture, runSubjectFixture, stepRunFixture, stepRunResourceFixture, workflowResourceFixture } from "./testing";

export default { title: "Workflows/Run page", parameters: { layout: "fullscreen" } };
export const Recovery = { render: () => <RunStory /> };
export const Runs = { render: () => <RunStory list /> };
export const Waiting = { render: () => <RunStory waiting /> };
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
  id: `wsr_mapped_${index}`, node_key: "mapped", is_mapped: true, map_index: index, rank: 0,
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
  ]),
  routesByResource: {
    "workflows.WorkflowRun": { collection: "workflows.runs", record: { name: "workflows.runs.record", param: "id" } },
    "workflows.Workflow": { collection: "workflows.catalogue", record: { name: "workflows.catalogue.record", param: "id" } },
    "notes.Note": { collection: "notes", record: { name: "notes.record", param: "id" } },
  },
  auth: { user: { id: "usr_operator", name: "Operator" }, status: "authenticated" as const, hasRole: () => false },
};

function RunRoute() { return useRouteParam("id") ? <RunPage /> : <RunsPage />; }

/** Real router, query transport and generated mutation documents over retained fixture rows. */
export function RunStory({ list = false, waiting = false, redacted = false, unavailable = false, queryError = false, rejectAction = false,
  run, steps, onRequest }: {
  list?: boolean; waiting?: boolean; redacted?: boolean; unavailable?: boolean; queryError?: boolean; rejectAction?: boolean;
  run?: Run; steps?: readonly StepRun[]; onRequest?: (request: RunRequest) => void;
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
        : jsonResponse({ data: { workflowrun_by_pk: unavailable ? null : current } });
      if (query.includes("notes_by_pk")) return jsonResponse({ data: { notes_by_pk: { id: "nte_7", display_name: "Review notes" } } });
      if (query.includes("steprun_by_pk")) return jsonResponse({ data: { steprun_by_pk: currentSteps.find((step) => step.id === variables.id) ?? null } });
      if (/\bsteprun(?:\s*\(|\s*\{)/.test(query)) {
        const offset = typeof variables.offset === "number" ? variables.offset : 0;
        const limit = typeof variables.limit === "number" ? variables.limit : currentSteps.length;
        return jsonResponse({ data: { steprun: currentSteps.slice(offset, offset + limit), steprun_aggregate: { aggregate: { count: currentSteps.length } } } });
      }
      if (query.includes("steprun_aggregate")) return jsonResponse({ data: { steprun_aggregate: { aggregate: { count: currentSteps.length } } } });
      if (query.includes("workflowrun_groups")) return jsonResponse({ data: { workflowrun_groups: [{
        key: { status: current.status, workflow_id: current.version?.workflow?.id, workflow__name: current.version?.workflow?.name },
        aggregate: { count: 1 },
      }], totalCount: 1 } });
      if (/\bworkflow\s*\(/.test(query)) return jsonResponse({ data: { workflow: [current.version?.workflow], workflow_aggregate: { aggregate: { count: 1 } } } });
      return jsonResponse({ data: { workflowrun: [current], workflowrun_aggregate: { aggregate: { count: 1 } } } });
    }).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [
      runResourceFixture, stepRunResourceFixture, workflowResourceFixture, runSubjectFixture,
      ...["StepAttempt", "StepArtifact"].map((name) => testDataResource(`workflows.${name}`, {
        capabilities: ["list", "detail"], roots: { list: name.toLowerCase(), detail: `${name.toLowerCase()}_by_pk` },
      })),
    ] } } } };
  }, [waiting, redacted, unavailable, queryError, rejectAction, run, steps, onRequest]);
  return <RoutedRuntimeFixture activeSchema="console" schemas={schemas} collectionPath="/workflows/runs"
    initialEntry={list ? "/workflows/runs" : "/workflows/runs/wfr_review"} runtime={runtime}
    resourceName="workflows.WorkflowRun" resourceLabel="Runs" operationDocuments={documents}>
    <RunRoute />
  </RoutedRuntimeFixture>;
}

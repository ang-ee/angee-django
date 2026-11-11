import { useMemo, type ComponentProps, type ReactNode } from "react";
import { operationDocuments } from "@angee/gql/console/actions";
import { GraphQLError, storyAppearance, type RuntimeFixtureProps, RoutedRuntimeFixture, storySchema, type StoryRequest } from "@angee/storybook/testing";
import { ChatterTabsTestHost, ShellPageTestProviders } from "@angee/app/testing";
import { createRouteHref, defaultWidgets } from "@angee/ui";

import { decisionFixture, decisionResourceFixture } from "@angee/decisions/testing";
import { RunsPage } from "./RunsPage";
import type { Run, StepRun } from "./testing/documents.console";
import { runFixture, runGraphFixture, runGraphReadFixture, runResourceFixture, recordResourceFixture, runSubjectFixture, stepRunFixture, stepRunResourceFixture, workflowResourceFixture, attemptResourceFixture, userResourceFixture, watchResourceFixture } from "./testing";
import type { RunGraphData } from "./run-graph";
import { workflowVersionFixture } from "./catalogue/testing";
import { triggerEventResourceFixture } from "./trigger-testing";
import { WORKFLOW_STATUS_TONES } from "./status-tones";

export default { title: "Workflows/Run page", parameters: { angeeOwnRuntime: true, layout: "fullscreen" }, excludeStories: ["RunStory"] };
export const Recovery = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunStory appearance={storyAppearance(globals)} /> };
export const Runs = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunStory appearance={storyAppearance(globals)} list /> };
export const Waiting = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunStory appearance={storyAppearance(globals)} waiting /> };
export const Watching = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunStory appearance={storyAppearance(globals)}
  run={runFixture({ status: "WAITING", can_cancel: true, can_reprocess: false, finished_at: null })}
  steps={[stepRunFixture({ status: "WAITING", outcome: "", waiting_kind: "RECORD", can_retry: false,
    watches: [{ id: "wsw_note", record_model: "notes.Note", record_id: "nte_7" }] })]} /> };
export const DuplicateRisk = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunStory appearance={storyAppearance(globals)} steps={[stepRunFixture({ requires_duplicate_acknowledgement: true })]} /> };
export const ReadOnly = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunStory appearance={storyAppearance(globals)} run={runFixture({ can_reprocess: false })} steps={[stepRunFixture({ can_retry: false })]} /> };
export const Mapped = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunStory appearance={storyAppearance(globals)} steps={mappedSteps} /> };
export const Redacted = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunStory appearance={storyAppearance(globals)} redacted /> };
export const Unavailable = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunStory appearance={storyAppearance(globals)} unavailable /> };
export const QueryError = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunStory appearance={storyAppearance(globals)} queryError /> };
export const ActionRejected = { render: (_args: unknown, { globals }: { globals: Record<string, unknown> }) => <RunStory appearance={storyAppearance(globals)} rejectAction /> };

const mappedSteps = Array.from({ length: 11 }, (_, index) => stepRunFixture({
  id: `wsr_mapped_${index}`, node_key: "mapped.body", is_mapped: true, map_index: index, rank: 0,
  can_retry: false, attempts: [], records: [],
}));
const documents = { console: operationDocuments };
const runtime = {
  statusTones: WORKFLOW_STATUS_TONES,
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
export function RunStory({ appearance, list = false, waiting = false, redacted = false, unavailable = false, queryError = false, rejectAction = false,
  run, steps, children, evidence = [], onRequest, content, graph, initialEntry, liveProvider, queryClient }: {
  appearance?: RuntimeFixtureProps["appearance"];
  list?: boolean; waiting?: boolean; redacted?: boolean; unavailable?: boolean; queryError?: boolean; rejectAction?: boolean;
  run?: Run; steps?: readonly StepRun[]; children?: readonly Run[];
  evidence?: readonly { id: string; record_model: string | null; record_id: string | null }[];
  onRequest?: (request: StoryRequest) => void;
  content?: ReactNode;
  graph?: RunGraphData;
  initialEntry?: string;
  liveProvider?: ComponentProps<typeof RoutedRuntimeFixture>["liveProvider"];
  queryClient?: ComponentProps<typeof RoutedRuntimeFixture>["queryClient"];
}) {
  const schemas = useMemo(() => {
    let current = run ?? runFixture();
    let currentSteps = [...(steps ?? [stepRunFixture()])];
    if (waiting) {
      current = { ...current, status: "WAITING", can_cancel: true, can_reprocess: false, finished_at: null };
      currentSteps = currentSteps.map((step) => ({
      ...step, status: "WAITING", waiting_kind: "ERROR", wait_reason: "Dispatch attempts exhausted.",
      }));
    }
    if (redacted) currentSteps = currentSteps.map((step) => ({
      ...step, attempts: step.attempts.map((attempt) => ({ ...attempt, error: "", stacktrace: "" })),
    }));
    const messages: Record<string, string> = {
      cancel_workflow_run: "Open work canceled; the retained run is unchanged.",
      reprocess_workflow_run: "Run reprocessed.",
      retry_step: "Step retried; the run is waiting.",
      retry_step_accepting_duplicate: "Step retried with duplicate risk acknowledged.",
    };
    const action = (name: string) => (args: Record<string, unknown>) => {
      if (rejectAction) return { ok: false, message: "This step cannot be retried in place.", validation_errors: {} };
      if (name.startsWith("retry_step")) {
        current = { ...current, status: "WAITING", can_cancel: true, can_reprocess: false, finished_at: null };
        currentSteps = currentSteps.map((step) => step.id === args.id
          ? { ...step, status: "READY", can_retry: false, waiting_kind: null, wait_reason: "" } : step);
      }
      return { ok: true, message: messages[name], id: name === "reprocess_workflow_run" ? "wfr_replacement" : String(args.id) };
    };
    const scope = (args: Record<string, unknown>) => JSON.stringify(args.where ?? {});
    // A step-scoped evidence read lists the steps' records; the run's own scope lists its retained evidence.
    const evidenceRows = (args: Record<string, unknown>) => scope(args).includes("step_run") ? currentSteps.flatMap((step) => step.records) : evidence;
    // A child-runs scope lists the children; every other run scope lists the run itself.
    const runRows = (args: Record<string, unknown>) => scope(args).includes("parent_step__run") ? children ?? [] : [current];
    const stepRows = (args: Record<string, unknown>) => {
      if (scope(args).includes('"decision":{"_is_null":false}')) return currentSteps.filter((step) => step.decision);
      const offset = typeof args.offset === "number" ? args.offset : 0;
      const limit = typeof args.limit === "number" ? args.limit : currentSteps.length;
      return currentSteps.slice(offset, offset + limit);
    };
    const count = (rows: readonly unknown[]) => ({ aggregate: { count: rows.length } });
    const fixture = storySchema({
      cancel_workflow_run: action("cancel_workflow_run"),
      reprocess_workflow_run: action("reprocess_workflow_run"),
      retry_step: action("retry_step"),
      retry_step_accepting_duplicate: action("retry_step_accepting_duplicate"),
      workflowrun_by_pk: (args, { operationName }) => {
        if (queryError) throw new GraphQLError("The run could not be loaded.");
        return unavailable ? null : { ...current, id: args.id,
          ...(operationName === "WorkflowRunGraph" ? runGraphReadFixture(graph ?? runGraphFixture(currentSteps[0])) : {}) };
      },
      workflow_by_pk: () => current.version?.workflow,
      user_by_pk: () => current.run_as,
      triggerevent_by_pk: () => ({ id: "wte_review", display_name: "Review event" }),
      notes_by_pk: () => ({ id: "nte_7", display_name: "Review notes" }),
      steprecord: evidenceRows,
      steprecord_aggregate: (args) => count(evidenceRows(args)),
      steprun_by_pk: (args, { operationName }) => {
        const step = currentSteps.find((step) => step.id === args.id);
        return step ? { ...step, ...(operationName === "StepDecision" ? { decision: step.decision ? decisionFixture() : null } : {}) } : null;
      },
      stepattempt_by_pk: (args) => currentSteps.flatMap((step) => step.attempts).find((attempt) => attempt.id === args.id),
      stepattempt: () => currentSteps.flatMap((step) => step.attempts),
      stepattempt_aggregate: () => count(currentSteps.flatMap((step) => step.attempts)),
      stepwatch: () => currentSteps.flatMap((step) => step.watches),
      stepwatch_aggregate: () => count(currentSteps.flatMap((step) => step.watches)),
      steprun: stepRows,
      steprun_aggregate: () => count(currentSteps),
      decisions: () => [{ id: "dcn_review", kind: "review", verdict: null, answered_at: null }],
      decisions_aggregate: () => ({ aggregate: { count: 1 } }),
      // The leaf groups read carries each group's first page of the same scoped rows.
      workflowrun_groups: (args, { variables }) => runRows(args).slice(0, 1).map((row) => ({
        key: { status: row.status, origin: row.origin, version__workflow_id: row.version?.workflow?.id,
          version__workflow__name: row.version?.workflow?.name },
        aggregate: { count: runRows(args).length },
        ...(variables.with_rows ? { rows: runRows(args) } : {}),
      })),
      workflowrun_groups_count: (args) => Math.min(runRows(args).length, 1),
      workflow: () => [current.version?.workflow],
      workflow_aggregate: () => ({ aggregate: { count: 1 } }),
      workflowrun: runRows,
      workflowrun_aggregate: (args) => count(runRows(args)),
    }, onRequest).public!;
    return { public: fixture, console: { ...fixture, metadata: { angee: { resources: [
      runResourceFixture, recordResourceFixture, stepRunResourceFixture, workflowResourceFixture, runSubjectFixture,
      workflowVersionFixture, attemptResourceFixture, userResourceFixture,
      triggerEventResourceFixture, watchResourceFixture, decisionResourceFixture,
    ] } } } };
  }, [waiting, redacted, unavailable, queryError, rejectAction, run, steps, children, evidence, onRequest, graph]);
  return <RoutedRuntimeFixture appearance={appearance} activeSchema="console" schemas={schemas} collectionPath="/workflows/runs"
    initialEntry={initialEntry ?? (list ? "/workflows/runs" : "/workflows/runs/wfr_review")} runtime={runtime}
    liveProvider={liveProvider} queryClient={queryClient}
    resourceName="workflows.WorkflowRun" resourceLabel="Runs" operationDocuments={documents}>
    <ShellPageTestProviders runtime={{ ...runtime, widgets: defaultWidgets }}>
      {content ?? <div className="grid min-h-0 flex-1 grid-cols-[1fr_24rem] gap-4"><RunsPage /><ChatterTabsTestHost /></div>}
    </ShellPageTestProviders>
  </RoutedRuntimeFixture>;
}

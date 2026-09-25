import * as React from "react";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  Badge,
  Button,
  Column,
  cn,
  EmptyState,
  errorMessage,
  ErrorBanner,
  Checkbox,
  FieldDescriptorControl,
  Field,
  FieldLabel,
  FieldRoot,
  Form,
  GraphView,
  List,
  RelationFieldWidget,
  ResourceList,
  Skeleton,
  SkeletonStatus,
  useStatusTone,
  TextLink,
  Workbench,
  useContainerQuery,
  useResourceRecordHrefLookup,
  useRouteHref,
  useRouteSearch,
  updateRouteSearch,
  type RecordTabDescriptor,
  type StringIdRow,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import {
  WorkflowGraphDocument,
  WorkflowAttemptPayloadDocument,
  WorkflowInspectionSelectionDocument,
  WorkflowAttemptArtifactsDocument,
  WorkflowRecoveryPlanDocument,
  WorkflowTestRepairContextDocument,
  StartWorkflowRecoveryDocument,
  WorkflowRunInspectionDocument,
  WorkflowStepRunCandidateDocument,
} from "../documents.console";
import { useWorkflowsT } from "../i18n";
import {
  workflowGraphEdges,
  workflowGraphNodes,
  workflowNodeStyles,
} from "./graph-data";

const WORKFLOW_MODEL = "workflows.Workflow";
const RUN_MODEL = "workflows.WorkflowRun";
const STEP_RUN_MODEL = "workflows.StepRun";
const STEP_ATTEMPT_MODEL = "workflows.StepAttempt";
const ARTIFACT_MODEL = "workflows.StepArtifact";
export const TERMINAL_RUN_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELED"]);

interface StepAttemptRow extends StringIdRow {
  status?: unknown;
  result_kind?: unknown;
  applied_at?: unknown;
  lease_revoked_at?: unknown;
}

interface StepRunRow extends StringIdRow {
  map_index?: unknown;
}

interface StepArtifactRow extends StringIdRow {
  label?: unknown;
  target_reference?: { model?: string; id?: string } | null;
}

export function RunTimelinePanel({ runId, onReprocess, reprocessing = false }: { runId: string; onReprocess?: () => Promise<void>; reprocessing?: boolean }): React.ReactElement {
  const t = useWorkflowsT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const search = useRouteSearch();
  const executionId = typeof search.execution === "string" ? search.execution : null;
  const attemptId = typeof search.attempt === "string" ? search.attempt : null;
  const showingAttemptHistory = search.history === "attempts";
  const showingExecutionHistory = search.history === "executions";
  const selectedStepId = typeof search.step === "string" ? search.step : null;
  const [containerRef, wide] = useContainerQuery(960);
  const runQuery = useAuthoredQuery(
    WorkflowRunInspectionDocument,
    { run: runId },
    {
      models: [RUN_MODEL, STEP_RUN_MODEL],
      records: [{ model: RUN_MODEL, id: runId }],
      relatedModels: [STEP_RUN_MODEL],
    },
  );
  const workflowId = runQuery.data?.workflow_runs_by_pk?.workflow.id ?? "";
  const graphQuery = useAuthoredQuery(
    WorkflowGraphDocument,
    { workflow: workflowId },
    {
      enabled: workflowId.length > 0,
      models: [WORKFLOW_MODEL],
      records: [{ model: WORKFLOW_MODEL, id: workflowId }],
    },
  );
  const selectionQuery = useAuthoredQuery(
    WorkflowInspectionSelectionDocument,
    { run: runId, execution: executionId ?? "", attempt: attemptId ?? "" },
    {
      enabled: Boolean(executionId),
      models: [STEP_RUN_MODEL, STEP_ATTEMPT_MODEL],
      records: [
        { model: STEP_RUN_MODEL, id: executionId ?? "" },
        ...(attemptId ? [{ model: STEP_ATTEMPT_MODEL, id: attemptId }] : []),
      ],
      relatedModels: [STEP_ATTEMPT_MODEL],
    },
  );
  const candidateQuery = useAuthoredQuery(
    WorkflowStepRunCandidateDocument,
    { run: runId, step: selectedStepId ?? "" },
    {
      enabled: Boolean(selectedStepId) && !executionId,
      models: [RUN_MODEL, STEP_RUN_MODEL],
      records: [{ model: RUN_MODEL, id: runId }],
      relatedModels: [STEP_RUN_MODEL],
    },
  );
  const statusByStep = React.useMemo(() => {
    const aggregate = new Map<string, { status: string; counts: Map<string, number> }>();
    for (const group of runQuery.data?.workflow_step_runs_groups ?? []) {
      const stepId = group.key.step_id;
      if (!stepId) continue;
      const status = String(group.key.status ?? "");
      const current = aggregate.get(stepId);
      aggregate.set(stepId, {
        status: !current || statusPriority(status) > statusPriority(current.status) ? status : current.status,
        counts: new Map(current?.counts).set(status, (current?.counts.get(status) ?? 0) + group.aggregate.count),
      });
    }
    const result = new Map<string, { status: string; detail: React.ReactNode }>();
    for (const [stepId, value] of aggregate) result.set(stepId, {
      status: value.status,
      detail: [...value.counts]
        .sort(([left], [right]) => statusPriority(right) - statusPriority(left))
        .map(([status, count]) => `${count} ${status.toLowerCase()}`)
        .join(" · "),
    });
    return result;
  }, [runQuery.data?.workflow_step_runs_groups]);
  const graphNodes = React.useMemo(
    () => workflowGraphNodes(graphQuery.data?.workflow_steps ?? [], statusByStep, false)
      .map((node) => ({ ...node, selected: node.id === selectedStepId })),
    [graphQuery.data?.workflow_steps, selectedStepId, statusByStep],
  );
  const graphEdges = React.useMemo(
    () => workflowGraphEdges(graphQuery.data?.workflow_edges ?? []),
    [graphQuery.data?.workflow_edges],
  );
  const selectedExecution = selectionQuery.data?.workflow_step_runs[0];
  const validExecution = executionId != null
    && selectedExecution?.id === executionId
    && (selectedStepId == null || selectedExecution.step?.id === selectedStepId);
  const validAttempt = attemptId == null
    || selectionQuery.data?.workflow_step_attempts[0]?.id === attemptId;
  const currentAttemptId = selectionQuery.data?.workflow_step_runs[0]?.current_attempt?.id ?? null;
  const attemptCount = selectionQuery.data?.workflow_step_attempts_aggregate.aggregate.count ?? 0;
  const executionSummary = selectedExecution ? [
    selectedExecution.step?.name || selectedExecution.step?.key || selectedExecution.system_kind || t("runs.systemExecution"),
    selectedExecution.map_index >= 0 ? t("runs.mapItem", { index: selectedExecution.map_index }) : null,
    selectedExecution.outcome || String(selectedExecution.status).toLowerCase(),
  ].filter(Boolean).join(" · ") : t("runs.attemptsForExecution");
  React.useEffect(() => {
    if (!validExecution || attemptId || showingAttemptHistory || !currentAttemptId) return;
    void navigate({
      to: ".",
      search: updateRouteSearch({
        attempt: currentAttemptId,
        history: undefined,
      }),
      replace: true,
    });
  }, [attemptId, currentAttemptId, navigate, showingAttemptHistory, validExecution]);
  React.useEffect(() => {
    const candidates = candidateQuery.data?.workflow_step_runs ?? [];
    if (executionId || showingExecutionHistory || candidates.length !== 1) return;
    void navigate({
      to: ".",
      search: updateRouteSearch({
        execution: candidates[0]!.id,
        attempt: undefined,
        history: undefined,
      }),
      replace: true,
    });
  }, [candidateQuery.data?.workflow_step_runs, executionId, navigate, showingExecutionHistory]);

  if (runQuery.isFetching && !runQuery.data) return <RunInspectorSkeleton label={t("runs.loading")} />;
  if (runQuery.error && !runQuery.data) return <ErrorBanner description={errorMessage(runQuery.error, t("runs.unavailable"))} />;
  const run = runQuery.data?.workflow_runs_by_pk;
  if (!run) return <EmptyState fill icon="workflow-run" title={t("runs.unavailable")} />;
  const runWaitingLabel = waitLabel(run.waiting_kind, run.status, t);
  const repairSource = run.test_repair_source_attempt;
  const recoverySource = run.recovery_source_attempt;
  const runSourceLabel = run.origin === "TEST"
    ? t("runs.testRevision", { revision: run.workflow.draft_revision })
    : run.origin === "RECOVERY"
      ? t("runs.recoveryRevision", { revision: run.workflow.status === "TEST" ? run.workflow.draft_revision : run.workflow.version ?? "?" })
      : t("runs.productionVersion", { version: run.workflow.version ?? "?" });
  const failedExecution = runQuery.data?.failed_step_runs[0];
  const failedAttemptId = failedExecution?.current_attempt?.id ?? null;
  const failedStepLabel = failedExecution?.step?.name || failedExecution?.step?.key
    || failedExecution?.system_kind || t("runs.systemExecution");
  const failedError = failedExecution?.current_attempt?.error || failedExecution?.error || run.error
    || t("runs.failedSummaryFallback");
  const activeAdvanceError = TERMINAL_RUN_STATUSES.has(String(run.status)) ? null : run.error;
  const setExecution = (id: string | null) => {
    void navigate({ to: ".", search: updateRouteSearch({ execution: id ?? undefined, attempt: undefined, history: id ? undefined : selectedStepId ? "executions" : undefined }) });
  };
  const setAttempt = (id: string | null) => {
    void navigate({ to: ".", search: updateRouteSearch({ attempt: id ?? undefined, history: id ? undefined : "attempts" }) });
  };
  const executionList = (
    <ResourceList resource={STEP_RUN_MODEL} scope="local" placement="inline" hideCreate pageSize={20} recordId={validExecution ? executionId : null} onSelect={setExecution} onClose={() => setExecution(null)} baseFilter={{ run: { exact: runId }, ...(selectedStepId ? { step: { exact: selectedStepId } } : {}) }}>
      <List resource={STEP_RUN_MODEL}><Column field="step" /><Column<StepRunRow> field="map_index" render={(row) => mapItemLabel(row.map_index, t)} /><Column field="status" widget="statusBadge" /><Column field="outcome" /><Column field="updated_at" /></List>
      <Form resource={STEP_RUN_MODEL}><Field name="step" readOnly title /><Field name="system_kind" readOnly /><Field name="map_index" readOnly /><Field name="status" readOnly widget="statusBadge" /><Field name="outcome" readOnly /><Field name="waiting_kind" readOnly /></Form>
    </ResourceList>
  );
  const attemptList = selectionQuery.error ? <ErrorBanner description={errorMessage(selectionQuery.error, t("runs.unavailable"))} />
    : selectionQuery.isFetching && !selectionQuery.data ? <RunListSkeleton label={t("runs.loading")} />
    : executionId && !validExecution ? <EmptyState fill icon="workflow-run" title={t("runs.unavailable")} />
    : attemptId && !validAttempt ? <EmptyState fill icon="workflow-run" title={t("runs.unavailable")} />
    : validExecution && attemptCount === 0 ? (
      <EmptyState fill icon="workflow-run" title={t(TERMINAL_RUN_STATUSES.has(String(selectedExecution?.status))
        ? "runs.noAttemptHistory" : "runs.awaitingFirstAttempt")} />
    ) : (
    <AttemptHistory executionId={validExecution ? executionId : ""} attemptId={validExecution && validAttempt ? attemptId : null}
      defaultRecordTab={String(selectedExecution?.status) === "FAILED" ? "failure" : "input"} onSelect={setAttempt} />
    );
  const graph = graphQuery.error ? <ErrorBanner description={errorMessage(graphQuery.error, t("runs.unavailable"))} /> : graphNodes.length === 0 ? (
    <EmptyState fill icon="workflow-canvas" title={t("canvas.emptyTitle")} description={t("runs.emptyTimeline")} />
  ) : <GraphView className="h-full" ariaLabel={t("runs.graph")} fitViewOptions={{ padding: 0.18, maxZoom: 1 }} nodes={graphNodes} edges={graphEdges} nodeStyles={workflowNodeStyles} onNodeSelect={(node) => {
    const nodeId = node?.id ?? null;
    if (nodeId === selectedStepId) return;
    void navigate({ to: ".", search: updateRouteSearch({ step: nodeId ?? undefined, execution: undefined, attempt: undefined, history: undefined }) });
  }} />;
  const narrowStack = (label: React.ReactNode, backLabel: React.ReactNode, onBack: () => void, content: React.ReactNode) => (
    <section aria-label={String(label)} className="flex h-full min-h-0 flex-col">
      <div className="flex flex-none items-center gap-3 border-b border-border-subtle px-2 py-1">
        <Button type="button" variant="ghost" onClick={onBack}>{backLabel}</Button>
        <span className="truncate text-13 font-medium">{label}</span>
      </div>
      <div className="min-h-0 flex-1">{content}</div>
    </section>
  );
  const narrowPane = attemptId
    ? narrowStack(executionSummary, t("runs.backToAttempts"), () => setAttempt(null), attemptList)
    : validExecution && showingAttemptHistory
      ? narrowStack(executionSummary, t("runs.backToExecutions"), () => setExecution(null), attemptList)
      : executionId
        ? narrowStack(executionSummary, t("runs.backToExecutions"), () => setExecution(null), attemptList)
      : selectedStepId
        ? narrowStack(t("runs.executionsForStep"), t("runs.backToGraph"), () => { void navigate({ to: ".", search: updateRouteSearch({ step: undefined, history: undefined }) }); }, executionList)
        : graph;
  return (
    <div ref={containerRef} className="flex h-full min-h-0 flex-col">
      <div className="flex-none border-b border-border-subtle bg-sheet px-4 py-2 text-13 text-fg-muted">
        {runSourceLabel} · {t("runs.executionCount", { count: runQuery.data?.workflow_step_runs_aggregate.aggregate.count ?? 0 })}
        {run.occurrence_id ? ` · ${t("runs.occurrence")}: ${String(run.occurrence_id)}` : ""}
      </div>
      {repairSource ? <div className="flex-none border-b border-border-subtle bg-sheet px-4 py-2 text-13 text-fg-muted">
        {t("runs.retestsAttempt")} <TextLink href={`${routeHref("workflows.run", { id: repairSource.step_run.run.id })}?execution=${encodeURIComponent(repairSource.step_run.id)}&attempt=${encodeURIComponent(repairSource.id)}`} onNavigate={(href) => { void navigate({ to: href }); }}>{t("runs.openSourceAttempt")}</TextLink>
      </div> : null}
      {recoverySource ? <div className="flex-none border-b border-border-subtle bg-sheet px-4 py-2 text-13 text-fg-muted">
        {t("runs.recoversAttempt")} <TextLink href={`${routeHref("workflows.run", { id: recoverySource.step_run.run.id })}?execution=${encodeURIComponent(recoverySource.step_run.id)}&attempt=${encodeURIComponent(recoverySource.id)}`} onNavigate={(href) => { void navigate({ to: href }); }}>{t("runs.openSourceAttempt")}</TextLink>
      </div> : null}
      {runWaitingLabel ? <div className="flex-none border-b border-border-subtle bg-sheet px-4 py-2 text-13 text-fg-muted">{runWaitingLabel}</div> : null}
      {activeAdvanceError ? <section className="flex-none border-b border-danger bg-danger-soft px-4 py-3" role="alert">
        <h2 className="font-medium text-danger-text">{t("runs.advanceError")}</h2>
        <p className="mt-1 text-13 text-danger-text">{activeAdvanceError}</p>
        <p className="mt-1 text-13 text-danger-text">{t("runs.advanceErrorHint")}</p>
      </section> : null}
      {run.status === "FAILED" ? <section className="flex-none border-b border-danger bg-danger-soft px-4 py-3" role="alert">
        <h2 className="font-medium text-danger-text">{t("runs.failedSummary", { step: failedStepLabel })}</h2>
        <p className="mt-1 text-13 text-danger-text">{failedError}</p>
        {failedExecution ? <div className="mt-3 flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="secondary" onClick={() => {
            void navigate({ to: ".", search: updateRouteSearch({
              step: failedExecution.step?.id,
              execution: failedExecution.id,
              attempt: failedAttemptId ?? undefined,
              history: undefined,
            }) });
          }}>{t("runs.inspectFailure")}</Button>
          {onReprocess ? <Button type="button" size="sm" disabled={reprocessing} onClick={() => {
            void onReprocess();
          }}>{reprocessing ? t("runs.reprocessing") : t("runs.reprocess")}</Button> : null}
        </div> : null}
        {failedAttemptId ? <details className="mt-3">
          <summary className="cursor-pointer text-13 font-medium text-fg">{t("runs.recoveryNext")}</summary>
          <AttemptRecoveryPanel attemptId={failedAttemptId} />
        </details> : null}
      </section> : null}
      {!wide ? <div className="min-h-0 flex-1">{narrowPane}</div> : (
        <Workbench
          autoSave="workflows.run-inspection.wide"
          contentMinSize={25}
          secondaryMinSize={35}
          secondarySize={55}
          secondary={executionId ? <div className="flex h-full min-h-0 flex-col"><div className="flex flex-none items-center gap-3 border-b border-border-subtle px-2 py-1"><Button type="button" variant="ghost" onClick={() => setExecution(null)}>{t("runs.backToExecutions")}</Button><span className="truncate text-13 font-medium">{executionSummary}</span></div><div className="min-h-0 flex-1">{attemptList}</div></div> : executionList}
          className="h-full min-h-0 bg-canvas"
        >
          {graph}
        </Workbench>
      )}
    </div>
  );
}

export function AttemptHistory({ executionId, attemptId, onSelect, defaultRecordTab = "input" }: {
  executionId: string;
  attemptId: string | null;
  onSelect: (id: string | null) => void;
  defaultRecordTab?: string;
}): React.ReactElement {
  const t = useWorkflowsT();
  const tabs = React.useMemo<readonly RecordTabDescriptor[]>(() => [
    { id: "input", label: t("runs.input"), render: ({ recordId }) => <AttemptPayloadPanel attemptId={recordId} stepRunId={executionId} pane="input" /> },
    { id: "output", label: t("runs.output"), render: ({ recordId }) => <AttemptPayloadPanel attemptId={recordId} stepRunId={executionId} pane="output" /> },
    { id: "checkpoint", label: t("runs.checkpoint"), render: ({ recordId }) => <AttemptPayloadPanel attemptId={recordId} stepRunId={executionId} pane="checkpoint" /> },
    { id: "failure", label: t("runs.failure"), render: ({ recordId }) => <AttemptPayloadPanel attemptId={recordId} stepRunId={executionId} pane="failure" /> },
    { id: "artifacts", label: t("runs.artifacts"), render: ({ recordId }) => <AttemptArtifactsPanel attemptId={recordId} /> },
    { id: "recovery", label: t("runs.recovery"), render: ({ recordId }) => <AttemptRecoveryPanel attemptId={recordId} /> },
  ], [executionId, t]);
  return (
    <ResourceList resource={STEP_ATTEMPT_MODEL} scope="local" placement="inline" hideCreate pageSize={20} recordId={attemptId} onSelect={onSelect} onClose={() => onSelect(null)} baseFilter={{ step_run: { exact: executionId } }} recordTabs={tabs} defaultRecordTab={defaultRecordTab}>
      <List<StepAttemptRow> resource={STEP_ATTEMPT_MODEL}><Column field="ordinal" /><Column<StepAttemptRow> field="status" render={(row) => attemptStateLabel(row, t)} /><Column<StepAttemptRow> field="result_kind" render={(row) => <AttemptResultBadge kind={row.result_kind} t={t} />} /><Column field="cause" /><Column field="applied_at" /><Column field="updated_at" /></List>
      <Form resource={STEP_ATTEMPT_MODEL}><Field name="ordinal" readOnly title /><Field name="status" readOnly /><Field name="cause" readOnly /><Field name="retry_of" readOnly /><Field name="retry_index" readOnly /><Field name="result_kind" readOnly /><Field name="outcome" readOnly /><Field name="applied_at" readOnly /><Field name="lease_revoked_at" readOnly /></Form>
    </ResourceList>
  );
}

function AttemptArtifactsPanel({ attemptId }: { attemptId: string }): React.ReactElement {
  const t = useWorkflowsT();
  const navigate = useNavigate();
  const recordHref = useResourceRecordHrefLookup();
  const query = useAuthoredQuery(
    WorkflowAttemptArtifactsDocument,
    { attempt: attemptId },
    { models: [ARTIFACT_MODEL] },
  );
  if (query.isFetching && !query.data) return <RunListSkeleton label={t("runs.loading")} />;
  if (query.error) return <ErrorBanner description={errorMessage(query.error, t("runs.unavailable"))} />;
  if (!query.data?.workflow_step_attempts[0]?.artifacts_present) {
    return <EmptyState icon="workflow-run" title={t("runs.artifactsNotRecorded")} />;
  }
  if (query.data.workflow_step_artifacts_aggregate.aggregate.count === 0) {
    return <EmptyState icon="workflow-run" title={t("runs.noArtifacts")} />;
  }
  return <ResourceList
    resource={ARTIFACT_MODEL}
    scope="local"
    placement="inline"
    hideCreate
    pageSize={20}
    baseFilter={{ attempt: { exact: attemptId } }}
  >
    <List<StepArtifactRow> resource={ARTIFACT_MODEL}>
      <Column field="declaration_index" header="#" />
      <Column<StepArtifactRow> field="label" header={t("runs.artifact")} render={(row) =>
        String(row.label || t("runs.artifact"))
      } />
      <Column<StepArtifactRow> field="target_reference" selectionPaths={["target_reference.model", "target_reference.id"]} header={t("runs.artifactTarget")} render={(row) => {
        const target = row.target_reference;
        const href = target?.model && target.id ? recordHref(target.model, target.id) : undefined;
        return href ? <TextLink href={href} onNavigate={(target) => { void navigate({ to: target }); }}>{t("runs.openArtifact")}</TextLink> : t("runs.artifactUnavailable");
      }} />
      <Column field="created_at" />
    </List>
  </ResourceList>;
}

export function AttemptRecoveryPanel({ attemptId }: { attemptId: string }): React.ReactElement {
  const t = useWorkflowsT();
  const uncertaintyAckId = React.useId();
  const navigate = useNavigate();
  const recordHref = useResourceRecordHrefLookup();
  const plan = useAuthoredQuery(
    WorkflowRecoveryPlanDocument,
    { sourceAttempt: attemptId },
    { models: [STEP_ATTEMPT_MODEL, RUN_MODEL] },
  );
  const repair = useAuthoredQuery(
    WorkflowTestRepairContextDocument,
    { sourceAttempt: attemptId },
    { models: [STEP_ATTEMPT_MODEL, RUN_MODEL, WORKFLOW_MODEL] },
  );
  const [start, { fetching, error, reset }] = useAuthoredMutation(StartWorkflowRecoveryDocument, {
    invalidateModels: [RUN_MODEL, STEP_RUN_MODEL, STEP_ATTEMPT_MODEL],
    errorFrom: (data) => data?.start_workflow_recovery.ok === false
      ? data.start_workflow_recovery.message : null,
  });
  const requestKey = React.useRef<string | null>(null);
  const currentAttempt = React.useRef(attemptId);
  const [uncertaintyAck, setUncertaintyAck] = React.useState(false);
  const [priorRecovery, setPriorRecovery] = React.useState("");
  const [started, setStarted] = React.useState<{ id: string; href?: string } | null>(null);
  const recovery = plan.data?.workflow_recovery_plan;
  const repairContext = repair.data?.workflow_test_repair_context;
  React.useEffect(() => {
    currentAttempt.current = attemptId;
    requestKey.current = null;
    reset();
    setStarted(null);
    setUncertaintyAck(false);
    setPriorRecovery("");
  }, [attemptId, reset]);
  const startRecovery = async () => {
    requestKey.current ??= crypto.randomUUID();
    try {
      const data = await start({
        sourceAttempt: attemptId,
        requestKey: requestKey.current,
        acknowledgeUncertainExternal: Boolean(recovery?.requires_uncertainty_ack && uncertaintyAck),
        priorRecovery: priorRecovery.trim() || null,
      });
      if (currentAttempt.current !== attemptId) return;
      const result = data?.start_workflow_recovery;
      if (result?.ok && result.id) {
        const href = recordHref(RUN_MODEL, result.id);
        setStarted({ id: result.id, href });
        if (href) void navigate({ to: href });
      }
    } catch {
      // The mutation state renders transport and domain errors.
    }
  };
  if ((plan.isFetching && !plan.data) || (repair.isFetching && !repair.data)) return <RunRecoverySkeleton label={t("runs.loading")} />;
  if (plan.error && !plan.data) return <ErrorBanner description={errorMessage(plan.error, t("runs.unavailable"))} />;
  return <div className="space-y-4 overflow-auto p-4">
    <div>
      <h3 className="font-medium text-fg">{t("runs.recovery")}</h3>
      <p className="mt-1 text-13 text-fg-muted">{recovery?.available
        ? t("runs.recoveryAvailable", { mode: recovery.mode ?? "" })
        : recovery?.unavailable_reason || t("runs.recoveryUnavailable")}</p>
      {recovery?.requires_uncertainty_ack ? <div className="mt-3 space-y-2">
        <p className="text-13 text-fg-muted">{recovery.uncertainty_reason}</p>
        <FieldRoot>
          <div className="flex items-start gap-2 text-13 text-fg">
            <Checkbox id={uncertaintyAckId} checked={uncertaintyAck}
              onCheckedChange={(checked) => setUncertaintyAck(checked)} />
            <FieldLabel htmlFor={uncertaintyAckId}>{t("runs.uncertainExternalAck")}</FieldLabel>
          </div>
        </FieldRoot>
      </div> : null}
      {recovery?.map_index != null ? <div className="mt-3 text-13 text-fg">
        <p>{t("runs.priorMapRecovery")}</p>
        <div className="mt-1">
          <RelationFieldWidget
            aria-label={t("runs.priorMapRecovery")}
            relation={{ resource: RUN_MODEL, labelField: "display_name", canCreate: false }}
            filters={[
              { field: "origin", operator: "eq", value: "RECOVERY" },
              { field: "status", operator: "in", value: ["SUCCEEDED", "FAILED", "CANCELED"] },
            ]}
            searchFields={["display_name"]}
            value={priorRecovery || null}
            onChange={setPriorRecovery}
            readOnly={fetching || Boolean(started)}
            placeholder={t("runs.priorMapRecoveryPlaceholder")}
          />
        </div>
        <span className="mt-1 block text-fg-muted">{t("runs.priorMapRecoveryHelp")}</span>
      </div> : null}
      {error ? <ErrorBanner description={errorMessage(error, t("runs.unavailable"))} /> : null}
      {started ? <p className="mt-2 text-13 text-fg-muted">
        {t("runs.recoveryStarted", { id: started.id })}
        {started.href ? <> · <TextLink href={started.href} onNavigate={(href) => { void navigate({ to: href }); }}>{t("runs.openRecovery")}</TextLink></> : null}
      </p> : null}
      <Button type="button" className="mt-3" disabled={!recovery?.available || fetching || Boolean(started) || Boolean(recovery?.requires_uncertainty_ack && !uncertaintyAck)} onClick={() => { void startRecovery(); }}>
        {fetching ? t("runs.recoveryStarting") : t("runs.startRecovery")}
      </Button>
    </div>
    {repairContext?.current_source_step_id ? <div className="border-t border-border-subtle pt-4">
      <h3 className="font-medium text-fg">{t("runs.testRepair")}</h3>
      <p className="mt-1 text-13 text-fg-muted">{t("runs.testRepairDescription")}</p>
      {recordHref(WORKFLOW_MODEL, repairContext.draft_workflow_id) ? <Button type="button" variant="secondary" className="mt-3" onClick={() => {
        const base = recordHref(WORKFLOW_MODEL, repairContext.draft_workflow_id);
        if (base) void navigate({ to: base, search: { repairAttempt: attemptId } });
      }}>{t("runs.testRepair")}</Button> : null}
    </div> : null}
  </div>;
}

export function AttemptPayloadPanel({ attemptId, stepRunId, pane }: { attemptId: string; stepRunId: string; pane: "input" | "output" | "checkpoint" | "failure" }): React.ReactElement {
  const t = useWorkflowsT();
  const query = useAuthoredQuery(WorkflowAttemptPayloadDocument, {
    attempt: attemptId,
    stepRun: stepRunId,
    includeInput: pane === "input",
    includeOutput: pane === "output",
    includeCheckpoint: pane === "checkpoint",
    includeFailure: pane === "failure",
  }, { enabled: Boolean(stepRunId), models: [STEP_ATTEMPT_MODEL] });
  if (query.isFetching && !query.data) return <RunPayloadSkeleton label={t("runs.loading")} />;
  if (query.error) return <ErrorBanner description={errorMessage(query.error, t("runs.unavailable"))} />;
  const attempt = query.data?.workflow_step_attempts[0];
  if (!attempt) return <EmptyState fill icon="workflow-run" title={t("runs.unavailable")} />;
  if (pane === "failure") return <div className="space-y-3 p-4">{attempt.error ? <ErrorBanner description={attempt.error} /> : null}{attempt.stacktrace ? <FieldDescriptorControl field={{ name: "stacktrace", label: t("runs.failure"), widget: "textarea" }} value={attempt.stacktrace} readOnly /> : null}</div>;
  const present = pane === "input" ? attempt.input_present : pane === "output" ? attempt.output_present : attempt.checkpoint_present;
  const value = pane === "input" ? attempt.input : pane === "output" ? attempt.output : attempt.checkpoint;
  return <div className="h-full overflow-auto p-4">{present ? <FieldDescriptorControl field={{ name: pane, label: t(pane === "input" ? "runs.input" : pane === "output" ? "runs.output" : "runs.checkpoint"), widget: "json" }} value={value} readOnly /> : <EmptyState icon="workflow-run" title={t("runs.payloadAbsent")} />}</div>;
}

function statusPriority(status: string): number {
  return ({ FAILED: 7, STARTED: 6, WAITING: 5, SCHEDULED: 4, CANCELED: 3, SUCCEEDED: 2, SKIPPED: 1 } as Record<string, number>)[status] ?? 0;
}

export function attemptStateLabel(row: StepAttemptRow, t: ReturnType<typeof useWorkflowsT>): string {
  const status = String(row.status ?? "").toLowerCase();
  if (status === "completed" && row.applied_at == null) return t("runs.returnedUnapplied");
  if (row.lease_revoked_at != null) return t("runs.revokedAttempt");
  return status || t("runs.unknownAttemptState");
}

function AttemptResultBadge({ kind, t }: { kind: unknown; t: ReturnType<typeof useWorkflowsT> }): React.ReactElement | null {
  const statusTone = useStatusTone();
  const value = String(kind ?? "").toUpperCase();
  if (!value) return null;
  const label = value === "ERROR" ? t("runs.resultERROR")
    : value === "WAIT" ? t("runs.resultWAIT")
      : value === "SUSPEND" ? t("runs.resultSUSPEND")
        : value === "DONE" ? t("runs.resultDONE") : value;
  return <Badge tone={statusTone(value)}>{label}</Badge>;
}

export function runCollectionFilter(
  collection: "automations" | "sessions",
): Record<string, unknown> {
  return {
    "workflow.purpose": {
      exact: collection === "sessions" ? "AGENT_SESSION" : "AUTOMATION",
    },
  };
}

export function runOriginLabel(origin: unknown, t: ReturnType<typeof useWorkflowsT>): string {
  return t(origin === "TEST" ? "runs.originTest" : origin === "RECOVERY" ? "runs.originRecovery" : "runs.originProduction");
}

export function mapItemLabel(index: unknown, t: ReturnType<typeof useWorkflowsT>): string {
  return typeof index === "number" && index >= 0 ? t("runs.mapItem", { index }) : "—";
}

function RunInspectorSkeleton({ label }: { label: string }): React.ReactElement {
  return (
    <SkeletonStatus label={label} className="flex h-full min-h-0 flex-col bg-canvas">
      <div aria-hidden="true" className="flex-none border-b border-border-subtle bg-sheet px-4 py-3">
        <Skeleton shape="text" size="sm" className="w-80 max-w-4/5" />
      </div>
      <div aria-hidden="true" className="grid min-h-0 flex-1 md:grid-cols-[minmax(0,1fr)_minmax(20rem,42%)]">
        <div className="relative min-h-72 overflow-hidden border-r border-border-subtle bg-inset p-5">
          <div className="grid h-full grid-cols-2 content-center gap-x-12 gap-y-8 lg:grid-cols-3">
            {Array.from({ length: 6 }, (_, index) => (
              <div key={index} className="rounded-8 border border-border-subtle bg-sheet p-3 shadow-xs">
                <Skeleton shape="text" size="md" className={index % 2 === 0 ? "w-4/5" : "w-2/3"} />
                <Skeleton shape="text" size="sm" className="mt-3 w-1/2" />
              </div>
            ))}
          </div>
        </div>
        <RunListSkeletonShape />
      </div>
    </SkeletonStatus>
  );
}

function RunListSkeleton({ label }: { label: string }): React.ReactElement {
  return (
    <SkeletonStatus label={label}>
      <RunListSkeletonShape />
    </SkeletonStatus>
  );
}

function RunListSkeletonShape(): React.ReactElement {
  return (
    <div aria-hidden="true" className="h-full min-h-0 overflow-hidden bg-sheet">
      <div className="flex items-center gap-3 border-b border-border-subtle px-3 py-3">
        <Skeleton className="h-btn-sm w-24" />
        <Skeleton className="h-btn-sm w-20" />
        <Skeleton shape="text" size="sm" className="ml-auto w-16" />
      </div>
      {Array.from({ length: 7 }, (_, index) => (
        <div key={index} className="grid grid-cols-[minmax(5rem,1fr)_minmax(4rem,.7fr)_minmax(5rem,.8fr)] gap-4 border-b border-border-subtle px-3 py-3">
          <Skeleton shape="text" size="sm" className={index % 2 === 0 ? "w-4/5" : "w-2/3"} />
          <Skeleton shape="text" size="sm" className="w-3/4" />
          <Skeleton shape="text" size="sm" className="ml-auto w-4/5" />
        </div>
      ))}
    </div>
  );
}

function RunPayloadSkeleton({ label }: { label: string }): React.ReactElement {
  return (
    <SkeletonStatus label={label} className="h-full min-h-40 overflow-hidden p-4">
      <div aria-hidden="true" className="space-y-3">
        <Skeleton shape="text" size="md" className="w-28" />
        <div className="rounded-8 border border-border-subtle bg-inset p-4">
          {Array.from({ length: 8 }, (_, index) => (
            <Skeleton
              key={index}
              shape="text"
              size="sm"
              className={cn("mb-3", index % 3 === 0 ? "w-4/5" : index % 3 === 1 ? "ml-4 w-2/3" : "ml-8 w-1/2")}
            />
          ))}
        </div>
      </div>
    </SkeletonStatus>
  );
}

function RunRecoverySkeleton({ label }: { label: string }): React.ReactElement {
  return (
    <SkeletonStatus label={label} className="space-y-4 overflow-hidden p-4">
      <div aria-hidden="true" className="space-y-3">
        <Skeleton shape="text" size="md" className="w-32" />
        <Skeleton shape="text" size="sm" className="w-4/5" />
        <Skeleton shape="text" size="sm" className="w-2/3" />
        <Skeleton className="h-btn-sm w-28" />
        <div className="border-t border-border-subtle pt-4">
          <Skeleton shape="text" size="md" className="w-36" />
          <Skeleton shape="text" size="sm" className="mt-3 w-3/4" />
        </div>
      </div>
    </SkeletonStatus>
  );
}

export function waitLabel(
  waitingKind: unknown,
  status: unknown,
  t: ReturnType<typeof useWorkflowsT>,
): string | null {
  if (status !== "WAITING") return null;
  if (waitingKind === "SCHEDULED") return t("runs.waitScheduled");
  if (waitingKind === "APPROVAL") return t("runs.waitApproval");
  if (waitingKind === "EXTERNAL") return t("runs.waitExternal");
  if (waitingKind === "CHILDREN") return t("runs.waitChildren");
  return t("runs.waitUnknown");
}

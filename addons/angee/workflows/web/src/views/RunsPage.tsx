import * as React from "react";
import { rowPublicId } from "@angee/metadata";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  Action,
  Alert,
  Badge,
  Code,
  Column,
  EmptyState,
  ErrorBanner,
  Facet,
  Field,
  Form,
  GraphView,
  Group,
  List,
  LoadingPanel,
  ResourceList,
  SplitPane,
  SplitPaneHandle,
  SplitPanes,
  TimelineView,
  TopMenuTabs,
  cn,
  formatDateTime,
  statusTone,
  useContainerQuery,
  type ActionContext,
  type RecordTabDescriptor,
  type StringIdRow,
} from "@angee/ui";
import { useSearch } from "@tanstack/react-router";

import {
  CancelWorkflowRunDocument,
  WorkflowGraphDocument,
  WorkflowRunDetailDocument,
  type WorkflowRunStepRun,
} from "../documents.console";
import { useWorkflowsT } from "../i18n";
import {
  latestStepRunByStep,
  workflowGraphEdges,
  workflowGraphNodes,
  workflowNodeStyles,
} from "./graph-data";
import { JsonBlock } from "./JsonBlock";

const WORKFLOW_MODEL = "workflows.Workflow";
const STEP_MODEL = "workflows.Step";
const EDGE_MODEL = "workflows.Edge";
const RUN_MODEL = "workflows.WorkflowRun";
const STEP_RUN_MODEL = "workflows.StepRun";
const DECISION_MODEL = "workflows.Decision";
const TERMINAL_RUN_STATUSES = new Set(["SUCCEEDED", "FAILED", "CANCELED"]);

interface WorkflowRunRow extends StringIdRow {
  status?: unknown;
  waiting_kind?: unknown;
  next_wake_at?: unknown;
}

export function RunsPage(): React.ReactElement {
  const t = useWorkflowsT();
  const search = useSearch({ strict: false }) as Readonly<Record<string, unknown>>;
  const collection = search.tab === "sessions" ? "sessions" : "automations";
  const waitOptions = React.useMemo(
    () => [
      { value: "scheduled", label: t("runs.waitScheduled") },
      { value: "approval", label: t("runs.waitApproval") },
      { value: "external", label: t("runs.waitExternal") },
      { value: "children", label: t("runs.waitChildren") },
    ],
    [t],
  );
  // Correct as-is: WorkflowRun and Decision have changes(), while StepRun is read by an authored query.
  const [cancelRun] = useAuthoredMutation(CancelWorkflowRunDocument, {
    invalidateModels: [RUN_MODEL, STEP_RUN_MODEL, DECISION_MODEL],
    errorFrom: (data) =>
      data?.cancel_workflow_run.ok === false ? data.cancel_workflow_run.message : null,
  });
  const cancel = React.useCallback(
    async (context: ActionContext) => {
      const id = rowPublicId(context.record);
      if (!id) return;
      const data = await cancelRun({ id });
      context.refresh();
      return data?.cancel_workflow_run?.message;
    },
    [cancelRun],
  );
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(
    () => [
      {
        id: "timeline",
        label: t("tabs.timeline"),
        icon: "workflow-run",
        render: ({ recordId }) => <RunTimelinePanel runId={recordId} />,
        keepMounted: true,
      },
    ],
    [t],
  );

  return (
    <ResourceList
      resource={RUN_MODEL}
      placement="inline"
      routed
      hideCreate
      recordTabs={recordTabs}
      recordPresentation="workspace"
      defaultRecordTab="timeline"
      overviewTab={{ label: t("tabs.details"), position: "last" }}
      baseFilter={runCollectionFilter(collection)}
      toolbarActions={
        <TopMenuTabs
          tabs={[
            {
              id: "automations",
              label: t("runs.collectionAutomations"),
              icon: "workflow",
            },
            {
              id: "sessions",
              label: t("runs.collectionSessions"),
              icon: "workflow-run",
            },
          ]}
        />
      }
    >
      <List<WorkflowRunRow> resource={RUN_MODEL} defaultGroup={{ field: "status" }}>
        <Facet field="workflow" label={t("col.workflow")} />
        <Column field="workflow.name" header={t("col.workflow")} />
        <Column field="status" widget="statusBadge" />
        <Column<WorkflowRunRow>
          field="waiting_kind"
          header={t("runs.waitingFor")}
          render={(row) => waitLabel(row.waiting_kind, row.status, t)}
        />
        <Column field="next_wake_at" header={t("runs.nextWake")} />
        <Column field="steps_taken" />
        <Column field="updated_at" />
      </List>
      <Form resource={RUN_MODEL}>
        <Field name="workflow" readOnly title />
        <Group label={t("runs.timeline")} columns={2}>
          <Field name="status" readOnly widget="statusbar" />
          <Field name="waiting_kind" readOnly options={waitOptions} />
          <Field name="next_wake_at" readOnly />
          <Field name="steps_taken" readOnly />
          <Field name="updated_at" readOnly />
        </Group>
        <Field name="budget_spent" widget="json" readOnly />
        <Field name="error" readOnly />
        <Action
          id="cancel"
          label={t("form.cancel")}
          icon="workflow-cancel"
          danger
          run={cancel}
          visibleWhen={(record) => !TERMINAL_RUN_STATUSES.has(String(record.status))}
        />
      </Form>
    </ResourceList>
  );
}

function RunTimelinePanel({ runId }: { runId: string }): React.ReactElement {
  const t = useWorkflowsT();
  const [containerRef, wide] = useContainerQuery(760);
  const runQuery = useAuthoredQuery(
    WorkflowRunDetailDocument,
    { run: runId },
    { models: [RUN_MODEL, STEP_RUN_MODEL] },
  );
  const workflowId = runQuery.data?.workflow_runs_by_pk?.workflow.id ?? "";
  const graphQuery = useAuthoredQuery(
    WorkflowGraphDocument,
    { workflow: workflowId },
    {
      enabled: workflowId.length > 0,
      models: [WORKFLOW_MODEL, STEP_MODEL, EDGE_MODEL, STEP_RUN_MODEL],
    },
  );
  const stepRuns = runQuery.data?.workflow_step_runs ?? [];
  const statusByStep = React.useMemo(
    () => latestStepRunByStep(stepRuns),
    [stepRuns],
  );
  const graphNodes = React.useMemo(
    () => workflowGraphNodes(graphQuery.data?.workflow_steps ?? [], statusByStep),
    [graphQuery.data?.workflow_steps, statusByStep],
  );
  const graphEdges = React.useMemo(
    () => workflowGraphEdges(graphQuery.data?.workflow_edges ?? []),
    [graphQuery.data?.workflow_edges],
  );

  if (runQuery.isFetching && !runQuery.data) {
    return <LoadingPanel message={t("runs.loading")} />;
  }
  if (runQuery.error && !runQuery.data) {
    return <ErrorBanner description={errorMessage(runQuery.error)} />;
  }
  const run = runQuery.data?.workflow_runs_by_pk;
  const runWaitingLabel = waitLabel(run?.waiting_kind, run?.status, t);

  return (
    <div ref={containerRef} className="flex h-full min-h-0 flex-col">
      {runWaitingLabel ? (
        <div className="flex-none border-b border-border-subtle bg-sheet px-4 py-2 text-13 text-fg-muted">
          {runWaitingLabel}
          {run?.waiting_kind === "scheduled" && run.next_wake_at
            ? ` · ${formatDateTime(run.next_wake_at)}`
            : null}
        </div>
      ) : null}
      <SplitPanes
        autoSave={`workflows.run-timeline.${wide ? "wide" : "narrow"}`}
        panelIds={["journal", "graph"]}
        direction={wide ? "horizontal" : "vertical"}
        className="h-full min-h-0 bg-canvas"
      >
        <SplitPane id="journal" defaultSize={wide ? 46 : 55} minSize={30} collapsible>
          <TimelineView<WorkflowRunStepRun>
            rows={stepRuns}
            dateField="created_at"
            rowKey="id"
            emptyContent={t("runs.emptyTimeline")}
            renderEntry={(row) => <RunJournalEntry row={row} />}
          />
        </SplitPane>
        <SplitPaneHandle />
        <SplitPane id="graph" defaultSize={wide ? 54 : 45} minSize={30}>
          {graphQuery.error ? (
            <ErrorBanner description={errorMessage(graphQuery.error)} />
          ) : graphNodes.length === 0 ? (
            <EmptyState
              fill
              icon="workflow-canvas"
              title={t("canvas.emptyTitle")}
              description={t("runs.emptyTimeline")}
            />
          ) : (
            <GraphView
              className="h-full"
              nodes={graphNodes}
              edges={graphEdges}
              nodeStyles={workflowNodeStyles}
            />
          )}
        </SplitPane>
      </SplitPanes>
    </div>
  );
}

function RunJournalEntry({
  row,
}: {
  row: WorkflowRunStepRun;
}): React.ReactElement {
  const t = useWorkflowsT();
  const title = (row.step?.name ?? row.system_kind) || row.display_name;
  const waitingLabel = waitLabel(row.waiting_kind, row.status, t);
  return (
    <div className="space-y-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-13 font-semibold text-fg">{title}</span>
            {row.step?.key ? <Code tone="muted">{row.step.key}</Code> : null}
          </div>
          <div className="mt-1 text-xs text-fg-muted">
            {row.outcome || row.system_kind}
          </div>
          {waitingLabel ? (
            <div className="mt-1 text-xs text-fg-muted">
              {waitingLabel}
              {row.waiting_kind === "scheduled" && row.wait_until
                ? ` · ${formatDateTime(row.wait_until)}`
                : null}
            </div>
          ) : null}
        </div>
        <Badge tone={statusTone(row.status)}>{row.status}</Badge>
      </div>
      <div className="grid gap-2 lg:grid-cols-3">
        <JournalPayload title={t("runs.input")} value={row.input} />
        <JournalPayload title={t("runs.output")} value={row.output} />
        <JournalPayload title={t("runs.resume")} value={row.resume_state} />
      </div>
      {row.error ? (
        <Alert tone="danger">{row.error}</Alert>
      ) : null}
    </div>
  );
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

export function waitLabel(
  waitingKind: unknown,
  status: unknown,
  t: ReturnType<typeof useWorkflowsT>,
): string | null {
  if (status !== "WAITING") return null;
  if (waitingKind === "scheduled") return t("runs.waitScheduled");
  if (waitingKind === "approval") return t("runs.waitApproval");
  if (waitingKind === "external") return t("runs.waitExternal");
  if (waitingKind === "children") return t("runs.waitChildren");
  return t("runs.waitUnknown");
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function JournalPayload({
  title,
  value,
}: {
  title: string;
  value: unknown;
}): React.ReactElement {
  return (
    <section className={cn("min-w-0 space-y-1")}>
      <h4 className="text-xs font-semibold text-fg-muted">{title}</h4>
      <JsonBlock value={value} />
    </section>
  );
}

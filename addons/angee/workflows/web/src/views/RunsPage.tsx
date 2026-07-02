import * as React from "react";
import { rowPublicId } from "@angee/resources";
import {
  Action,
  Badge,
  Code,
  Column,
  EmptyState,
  Facet,
  Field,
  Form,
  GraphView,
  Group,
  List,
  LoadingPanel,
  ResourceList,
  TimelineView,
  cn,
  useAuthoredMutation,
  useAuthoredQuery,
  type ActionContext,
  type RecordTabDescriptor,
  type Tone,
} from "@angee/ui";

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

export function RunsPage(): React.ReactElement {
  const t = useWorkflowsT();
  const [cancelRun] = useAuthoredMutation(CancelWorkflowRunDocument, {
    invalidateModels: [RUN_MODEL, STEP_RUN_MODEL, DECISION_MODEL],
  });
  const cancel = React.useCallback(
    async (context: ActionContext) => {
      const id = rowPublicId(context.record);
      if (!id) return;
      const data = await cancelRun({ id });
      const result = data?.cancel_workflow_run;
      if (!result?.ok) throw new Error(result?.message ?? t("workflows.form.cancel"));
      context.refresh();
      return result.message;
    },
    [cancelRun, t],
  );
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(
    () => [
      {
        id: "timeline",
        label: t("workflows.tabs.timeline"),
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
    >
      <List resource={RUN_MODEL} defaultGroup={{ field: "status" }}>
        <Facet field="workflow" label="Workflow" labelField="name" />
        <Column field="workflow.name" header="Workflow" />
        <Column field="status" widget="statusBadge" />
        <Column field="steps_taken" />
        <Column field="wake_at" />
        <Column field="updated_at" />
      </List>
      <Form resource={RUN_MODEL}>
        <Field name="workflow" readOnly title />
        <Group label={t("workflows.runs.timeline")} columns={2}>
          <Field name="status" readOnly widget="statusbar" />
          <Field name="steps_taken" readOnly />
          <Field name="wake_at" readOnly />
          <Field name="updated_at" readOnly />
        </Group>
        <Field name="data" widget="json" readOnly />
        <Field name="budget_spent" widget="json" readOnly />
        <Field name="error" readOnly />
        <Action
          id="cancel"
          label={t("workflows.form.cancel")}
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
  const runQuery = useAuthoredQuery(
    WorkflowRunDetailDocument,
    { run: runId, runId },
    { models: [RUN_MODEL, STEP_RUN_MODEL] },
  );
  const workflowId = runQuery.data?.workflow_runs_by_pk?.workflow.id ?? "";
  const graphQuery = useAuthoredQuery(
    WorkflowGraphDocument,
    { workflow: workflowId, workflowId },
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

  if (runQuery.fetching && !runQuery.data) {
    return <LoadingPanel message={t("workflows.runs.loading")} />;
  }

  return (
    <div className="grid h-full min-h-[34rem] grid-cols-[minmax(0,0.92fr)_minmax(0,1.08fr)] overflow-hidden bg-canvas">
      <TimelineView<WorkflowRunStepRun & Record<string, unknown>>
        rows={stepRuns as readonly (WorkflowRunStepRun & Record<string, unknown>)[]}
        dateField="created_at"
        rowKey="id"
        emptyMessage={t("workflows.runs.emptyTimeline")}
        className="border-r border-border-subtle"
        renderEntry={(row) => <RunJournalEntry row={row} />}
      />
      <div className="min-h-0">
        {graphNodes.length === 0 ? (
          <EmptyState
            fill
            icon="workflow-canvas"
            title={t("workflows.canvas.emptyTitle")}
            description={t("workflows.runs.emptyTimeline")}
          />
        ) : (
          <GraphView
            className="h-full"
            nodes={graphNodes}
            edges={graphEdges}
            nodeStyles={workflowNodeStyles}
          />
        )}
      </div>
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
  return (
    <div className="space-y-3">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2">
            <span className="truncate text-13 font-semibold text-fg">{title}</span>
            {row.step?.key ? <Code tone="muted">{row.step.key}</Code> : null}
          </div>
          <div className="mt-1 text-xs text-fg-muted">
            {row.outcome || row.wait_event || row.system_kind}
          </div>
        </div>
        <Badge tone={toneForStepRun(row.status)}>{row.status}</Badge>
      </div>
      <div className="grid gap-2 lg:grid-cols-3">
        <JournalPayload title={t("workflows.runs.input")} value={row.input} />
        <JournalPayload title={t("workflows.runs.output")} value={row.output} />
        <JournalPayload title={t("workflows.runs.resume")} value={row.resume_state} />
      </div>
      {row.error ? (
        <div className="rounded-6 border border-danger-soft-border bg-danger-soft p-3 text-13 text-danger-soft-text">
          {row.error}
        </div>
      ) : null}
    </div>
  );
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

function toneForStepRun(status: string): Tone {
  if (status === "SUCCEEDED") return "success";
  if (status === "FAILED") return "danger";
  if (status === "WAITING") return "warning";
  if (status === "STARTED") return "info";
  return "neutral";
}

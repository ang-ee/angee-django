import * as React from "react";
import { rowPublicId } from "@angee/metadata";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  Action,
  Badge,
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
  ResourceEdit,
  ResourceList,
  useEnumOptions,
  useImplPrefill,
  type ActionContext,
  type GraphViewConnection,
  type GraphViewPosition,
  type RecordTabDescriptor,
} from "@angee/ui";

import {
  CreateWorkflowEdgeDocument,
  PublishWorkflowDocument,
  StartWorkflowRunDocument,
  UpdateWorkflowStepPositionDocument,
  WorkflowGraphDocument,
} from "../documents.console";
import { useWorkflowsT } from "../i18n";
import {
  workflowGraphEdges,
  workflowGraphNodes,
  workflowNodeStyles,
} from "./graph-data";

const WORKFLOW_MODEL = "workflows.Workflow";
const STEP_MODEL = "workflows.Step";
const EDGE_MODEL = "workflows.Edge";
const TRIGGER_MODEL = "workflows.Trigger";
const RUN_MODEL = "workflows.WorkflowRun";

export function WorkflowsPage(): React.ReactElement {
  const t = useWorkflowsT();
  const [publishWorkflow] = useAuthoredMutation(PublishWorkflowDocument, {
    invalidateModels: [WORKFLOW_MODEL, STEP_MODEL, EDGE_MODEL],
    errorFrom: (data) =>
      data?.publish_workflow.ok === false ? data.publish_workflow.message : null,
  });
  // Correct as-is: workflows.WorkflowRun declares changes(WorkflowRun, field="workflowRunChanged").
  const [startWorkflowRun] = useAuthoredMutation(StartWorkflowRunDocument, {
    invalidateModels: [RUN_MODEL],
    errorFrom: (data) =>
      data?.start_workflow_run.ok === false ? data.start_workflow_run.message : null,
  });
  const publish = React.useCallback(
    async (context: ActionContext) => {
      const id = rowPublicId(context.record);
      if (!id) return;
      const data = await publishWorkflow({ id });
      context.refresh();
      return data?.publish_workflow?.message;
    },
    [publishWorkflow],
  );
  const start = React.useCallback(
    async (context: ActionContext) => {
      const id = rowPublicId(context.record);
      if (!id) return;
      const data = await startWorkflowRun({ id });
      context.refresh();
      return data?.start_workflow_run?.message;
    },
    [startWorkflowRun],
  );
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(
    () => [
      {
        id: "canvas",
        label: t("tabs.canvas"),
        icon: "workflow-canvas",
        render: ({ recordId, reload }) => (
          <WorkflowCanvas workflowId={recordId} onChanged={reload} />
        ),
        keepMounted: true,
      },
      {
        id: "triggers",
        label: t("tabs.triggers"),
        icon: "workflow-trigger",
        render: ({ recordId }) => <WorkflowTriggersPanel workflowId={recordId} />,
      },
    ],
    [t],
  );

  return (
    <ResourceList
      resource={WORKFLOW_MODEL}
      placement="inline"
      routed
      recordTabs={recordTabs}
    >
      <List resource={WORKFLOW_MODEL} defaultGroup={{ field: "status" }}>
        <Facet field="status" label={t("col.status")} />
        <Column field="name" />
        <Column field="status" widget="statusBadge" />
        <Column field="version" />
        <Column field="updated_at" />
      </List>
      <Form resource={WORKFLOW_MODEL}>
        <Field name="name" title />
        <Field name="description" />
        <Group label={t("form.definition")} columns={2}>
          <Field name="status" readOnly widget="statusbar" />
          <Field name="version" readOnly />
          <Field name="error_workflow" />
          <Field name="max_steps" />
        </Group>
        <Field name="budget" widget="json" />
        <Action
          id="publish"
          label={t("form.publish")}
          icon="workflow-publish"
          run={publish}
          visibleWhen={(record) => record.status === "DRAFT"}
        />
        <Action
          id="start"
          label={t("form.start")}
          icon="workflow-run"
          run={start}
          visibleWhen={(record) => record.status !== "ARCHIVED"}
        />
      </Form>
    </ResourceList>
  );
}

function WorkflowCanvas({
  workflowId,
  onChanged,
}: {
  workflowId: string;
  onChanged?: () => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const graphQuery = useAuthoredQuery(
    WorkflowGraphDocument,
    { workflow: workflowId },
    { models: [WORKFLOW_MODEL, STEP_MODEL, EDGE_MODEL] },
  );
  // Correct as-is: WorkflowGraphDocument is an authored query keyed by STEP_MODEL.
  const [updatePosition] = useAuthoredMutation(UpdateWorkflowStepPositionDocument, {
    invalidateModels: [STEP_MODEL],
  });
  // Correct as-is: WorkflowGraphDocument is an authored query keyed by EDGE_MODEL.
  const [createEdge] = useAuthoredMutation(CreateWorkflowEdgeDocument, {
    invalidateModels: [EDGE_MODEL],
  });
  const [selectedStep, setSelectedStep] = React.useState<string | null>(null);
  const [selectedEdge, setSelectedEdge] = React.useState<string | null>(null);
  const [mutationError, setMutationError] = React.useState<string | null>(null);
  const workflow = graphQuery.data?.workflows_by_pk;
  const steps = graphQuery.data?.workflow_steps ?? [];
  const edges = graphQuery.data?.workflow_edges ?? [];
  const isDraft = workflow?.status === "DRAFT";
  const graphNodes = React.useMemo(() => workflowGraphNodes(steps), [steps]);
  const graphEdges = React.useMemo(() => workflowGraphEdges(edges), [edges]);
  const refreshGraph = React.useCallback(() => {
    onChanged?.();
  }, [onChanged]);
  const handleNodeDragEnd = React.useCallback(
    async (
      node: (typeof graphNodes)[number],
      position: GraphViewPosition,
    ) => {
      try {
        setMutationError(null);
        await updatePosition({ id: node.id, position });
        refreshGraph();
      } catch (error) {
        setMutationError(errorMessage(error));
      }
    },
    [refreshGraph, updatePosition],
  );
  const handleConnect = React.useCallback(
    async (edge: GraphViewConnection) => {
      if (edge.source === edge.target) return;
      try {
        setMutationError(null);
        await createEdge({
          workflow: workflowId,
          source: edge.source,
          target: edge.target,
          condition: "",
        });
        refreshGraph();
      } catch (error) {
        setMutationError(errorMessage(error));
      }
    },
    [createEdge, refreshGraph, workflowId],
  );

  if (graphQuery.fetching && !graphQuery.data) {
    return <LoadingPanel message={t("canvas.loading")} />;
  }

  return (
    <div className="grid h-full min-h-[34rem] grid-cols-[minmax(0,1fr)_minmax(18rem,22rem)] overflow-hidden bg-canvas">
      <div className="relative min-h-0 border-r border-border-subtle">
        {steps.length === 0 ? (
          <EmptyState
            fill
            icon="workflow-canvas"
            title={t("canvas.emptyTitle")}
            description={t("canvas.emptyDescription")}
          />
        ) : (
          <GraphView
            className="h-full"
            nodes={graphNodes}
            edges={graphEdges}
            nodeStyles={workflowNodeStyles}
            nodesDraggable={isDraft}
            onNodeDragEnd={isDraft ? handleNodeDragEnd : undefined}
            onConnect={isDraft ? handleConnect : undefined}
            onNodeSelect={(node) => {
              setSelectedStep(node?.id ?? null);
              if (node) setSelectedEdge(null);
            }}
            onEdgeSelect={(edge) => {
              setSelectedEdge(edge?.id ?? null);
              if (edge) setSelectedStep(null);
            }}
          />
        )}
        <div className="absolute left-3 top-3">
          <Badge tone={isDraft ? "warning" : "neutral"}>
            {isDraft ? t("canvas.draft") : t("canvas.readOnly")}
          </Badge>
        </div>
        <div className="absolute inset-x-3 bottom-3">
          <ErrorBanner description={mutationError} />
        </div>
      </div>
      <CanvasInspector
        selectedStep={selectedStep}
        selectedEdge={selectedEdge}
        onChanged={refreshGraph}
      />
    </div>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function CanvasInspector({
  selectedStep,
  selectedEdge,
  onChanged,
}: {
  selectedStep: string | null;
  selectedEdge: string | null;
  onChanged: () => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  if (selectedStep) {
    return <StepConfigPanel stepId={selectedStep} onChanged={onChanged} />;
  }
  if (selectedEdge) {
    return <EdgeConfigPanel edgeId={selectedEdge} onChanged={onChanged} />;
  }
  return (
    <div className="min-h-0 overflow-auto bg-sheet-1 p-4">
      <EmptyState
        icon="workflow-step"
        title={t("canvas.stepConfig")}
        description={t("canvas.selectStep")}
      />
    </div>
  );
}

function StepConfigPanel({
  stepId,
  onChanged,
}: {
  stepId: string;
  onChanged: () => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const stepClassOptions = useEnumOptions(STEP_MODEL, "step_class");
  const stepClassPrefill = useImplPrefill(STEP_MODEL, "step_class");
  const joinRuleOptions = useEnumOptions(STEP_MODEL, "join_rule");
  return (
    <div className="min-h-0 overflow-auto bg-sheet-1">
      <ResourceEdit resource={STEP_MODEL} id={stepId} onSaved={onChanged}>
        <Field name="name" title />
        <Group label={t("canvas.step")} columns={2}>
          <Field name="workflow" readOnly />
          <Field name="key" />
          <Field
            name="step_class"
            widget="select"
            options={stepClassOptions}
            prefill={stepClassPrefill}
          />
          <Field name="join_rule" widget="select" options={joinRuleOptions} />
          <Field name="is_entry" />
        </Group>
        <Field name="config" widget="json" />
      </ResourceEdit>
    </div>
  );
}

function EdgeConfigPanel({
  edgeId,
  onChanged,
}: {
  edgeId: string;
  onChanged: () => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  return (
    <div className="min-h-0 overflow-auto bg-sheet-1">
      <ResourceEdit resource={EDGE_MODEL} id={edgeId} onSaved={onChanged}>
        <Group label={t("canvas.edge")} columns={1}>
          <Field name="workflow" readOnly />
          <Field name="source" />
          <Field name="target" />
          <Field name="condition" />
        </Group>
      </ResourceEdit>
    </div>
  );
}

function WorkflowTriggersPanel({
  workflowId,
}: {
  workflowId: string;
}): React.ReactElement {
  const t = useWorkflowsT();
  const triggerKindOptions = useEnumOptions(TRIGGER_MODEL, "kind");
  return (
    <ResourceList
      resource={TRIGGER_MODEL}
      placement="inline"
      baseFilter={{ workflow: { exact: workflowId } }}
      createDefaults={{ workflow: workflowId }}
    >
      <List resource={TRIGGER_MODEL}>
        <Column field="kind" />
        <Column field="enabled" />
        <Column field="next_fire_at" />
        <Column field="updated_at" />
      </List>
      <Form resource={TRIGGER_MODEL}>
        <Group label={t("triggers.details")} columns={2}>
          <Field name="workflow" createOnly />
          <Field name="kind" widget="select" options={triggerKindOptions} createOnly />
          <Field name="enabled" />
          <Field name="next_fire_at" readOnly />
        </Group>
        <Field name="config" widget="json" />
      </Form>
    </ResourceList>
  );
}

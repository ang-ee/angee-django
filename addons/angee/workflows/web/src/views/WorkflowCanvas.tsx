import * as React from "react";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import {
  Badge,
  Button,
  EmptyState,
  ErrorBanner,
  Field,
  GraphView,
  Glyph,
  Group,
  LoadingPanel,
  ResourceEdit,
  ResourceShow,
  SplitPane,
  SplitPaneHandle,
  SplitPanes,
  useCollapsiblePane,
  useContainerQuery,
  useEnumOptions,
  useImplPrefill,
  type GraphViewConnection,
  type GraphViewPosition,
} from "@angee/ui";

import {
  CreateWorkflowEdgeDocument,
  UpdateWorkflowStepPositionDocument,
  WorkflowGraphDocument,
} from "../documents.console";
import { useWorkflowsT } from "../i18n";
import {
  workflowGraphEdges,
  workflowGraphNodes,
  workflowNodeStyles,
} from "./graph-data";
import { EMPTY_WORKFLOW_EDITOR_SELECTION, workflowEditorSelection } from "./workflow-editor-state";

const WORKFLOW_MODEL = "workflows.Workflow";
const STEP_MODEL = "workflows.Step";
const EDGE_MODEL = "workflows.Edge";

export function WorkflowCanvas({
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
  const [updatePosition] = useAuthoredMutation(UpdateWorkflowStepPositionDocument, {
    invalidateModels: [STEP_MODEL],
  });
  const [createEdge] = useAuthoredMutation(CreateWorkflowEdgeDocument, {
    invalidateModels: [EDGE_MODEL],
  });
  const [selection, dispatchSelection] = React.useReducer(
    workflowEditorSelection,
    EMPTY_WORKFLOW_EDITOR_SELECTION,
  );
  const selectedStep = selection.stepId;
  const selectedEdge = selection.edgeId;
  const showingInspector = selection.narrowView === "inspector";
  const [containerRef, wide] = useContainerQuery(760);
  const graphPane = useCollapsiblePane();
  const inspectorPane = useCollapsiblePane({ defaultCollapsed: true });
  const [mutationError, setMutationError] = React.useState<string | null>(null);
  const workflow = graphQuery.data?.workflows_by_pk;
  const steps = graphQuery.data?.workflow_steps ?? [];
  const edges = graphQuery.data?.workflow_edges ?? [];
  const isDraft = workflow?.status === "DRAFT";
  const hasSelection = selectedStep !== null || selectedEdge !== null;
  React.useEffect(() => {
    if (!graphPane.ready || !inspectorPane.ready) return;
    if (wide) {
      graphPane.expand();
      if (hasSelection) inspectorPane.expand();
      else inspectorPane.collapse();
    } else if (showingInspector && hasSelection) {
      graphPane.collapse();
      inspectorPane.expand();
    } else {
      graphPane.expand();
      inspectorPane.collapse();
    }
  }, [graphPane, hasSelection, inspectorPane, showingInspector, wide]);
  const graphNodes = React.useMemo(() => workflowGraphNodes(steps), [steps]);
  const graphEdges = React.useMemo(() => workflowGraphEdges(edges), [edges]);
  const refreshGraph = React.useCallback(() => {
    onChanged?.();
  }, [onChanged]);
  const handleNodeDragEnd = React.useCallback(
    async (node: (typeof graphNodes)[number], position: GraphViewPosition) => {
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

  if (graphQuery.isFetching && !graphQuery.data) {
    return <LoadingPanel message={t("canvas.loading")} />;
  }
  if (graphQuery.error && !graphQuery.data) {
    return <ErrorBanner description={errorMessage(graphQuery.error)} />;
  }

  return (
    <SplitPanes
      ref={containerRef}
      autoSave="workflows.canvas"
      persistLayout={wide}
      panelIds={["graph", "inspector"]}
      className="h-full min-h-0 bg-canvas"
    >
      <SplitPane
        id="graph"
        defaultSize={68}
        minSize={42}
        collapsible
        panelRef={graphPane.panelRef}
        onResize={graphPane.onResize}
        className={!wide && showingInspector ? "hidden" : "relative"}
      >
        {steps.length === 0 ? (
          <EmptyState fill icon="workflow-canvas" title={t("canvas.emptyTitle")} description={t("canvas.emptyDescription")} />
        ) : (
          <GraphView
            className="h-full"
            nodes={graphNodes}
            edges={graphEdges}
            nodeStyles={workflowNodeStyles}
            nodesDraggable={isDraft}
            onNodeDragEnd={isDraft ? handleNodeDragEnd : undefined}
            onConnect={isDraft ? handleConnect : undefined}
            onNodeClick={(node) => dispatchSelection({ type: "select-step", id: node.id })}
            onEdgeClick={(edge) => dispatchSelection({ type: "select-edge", id: edge.id })}
            onNodeSelect={(node) => { if (node) dispatchSelection({ type: "select-step", id: node.id }); }}
            onEdgeSelect={(edge) => { if (edge) dispatchSelection({ type: "select-edge", id: edge.id }); }}
          />
        )}
        <div className="absolute left-3 top-3">
          <Badge tone={isDraft ? "warning" : "neutral"}>
            {isDraft ? t("canvas.draft") : t("canvas.readOnlyVersion", { version: workflow?.version ?? "?" })}
          </Badge>
        </div>
        <div className="absolute inset-x-3 bottom-3"><ErrorBanner description={mutationError} /></div>
      </SplitPane>
      <SplitPaneHandle className={!wide ? "hidden" : undefined} />
      <SplitPane
        id="inspector"
        defaultSize={32}
        minSize={22}
        collapsible
        panelRef={inspectorPane.panelRef}
        onResize={inspectorPane.onResize}
        className={!wide && !showingInspector ? "hidden" : undefined}
      >
        {!wide && hasSelection ? (
          <Button type="button" variant="ghost" size="sm" onClick={() => dispatchSelection({ type: "show-canvas" })}>
            <Glyph name="chevron-left" />
            {t("canvas.back")}
          </Button>
        ) : null}
        <CanvasInspector selectedStep={selectedStep} selectedEdge={selectedEdge} readOnly={!isDraft} onChanged={refreshGraph} />
      </SplitPane>
    </SplitPanes>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function CanvasInspector({ selectedStep, selectedEdge, readOnly, onChanged }: { selectedStep: string | null; selectedEdge: string | null; readOnly: boolean; onChanged: () => void }): React.ReactElement {
  const t = useWorkflowsT();
  if (selectedStep) return <StepConfigPanel stepId={selectedStep} readOnly={readOnly} onChanged={onChanged} />;
  if (selectedEdge) return <EdgeConfigPanel edgeId={selectedEdge} readOnly={readOnly} onChanged={onChanged} />;
  return <div className="min-h-0 overflow-auto bg-sheet-1 p-4"><EmptyState icon="workflow-step" title={t("canvas.stepConfig")} description={t("canvas.selectStep")} /></div>;
}

function StepConfigPanel({ stepId, readOnly, onChanged }: { stepId: string; readOnly: boolean; onChanged: () => void }): React.ReactElement {
  const t = useWorkflowsT();
  const stepClassOptions = useEnumOptions(STEP_MODEL, "step_class");
  const stepClassPrefill = useImplPrefill(STEP_MODEL, "step_class");
  const joinRuleOptions = useEnumOptions(STEP_MODEL, "join_rule");
  const StepResource = readOnly ? ResourceShow : ResourceEdit;
  return (
    <div className="min-h-0 overflow-auto bg-sheet-1">
      <StepResource resource={STEP_MODEL} id={stepId} onSaved={onChanged}>
        <Field name="name" title />
        <Group label={t("canvas.step")} columns={2}>
          <Field name="workflow" readOnly />
          <Field name="key" />
          <Field name="step_class" widget="select" options={stepClassOptions} prefill={stepClassPrefill} />
          <Field name="join_rule" widget="select" options={joinRuleOptions} />
          <Field name="is_entry" />
        </Group>
        <Field name="config" widget="json" />
      </StepResource>
    </div>
  );
}

function EdgeConfigPanel({ edgeId, readOnly, onChanged }: { edgeId: string; readOnly: boolean; onChanged: () => void }): React.ReactElement {
  const t = useWorkflowsT();
  const EdgeResource = readOnly ? ResourceShow : ResourceEdit;
  return (
    <div className="min-h-0 overflow-auto bg-sheet-1">
      <EdgeResource resource={EDGE_MODEL} id={edgeId} onSaved={onChanged}>
        <Group label={t("canvas.edge")} columns={1}>
          <Field name="workflow" readOnly />
          <Field name="source" />
          <Field name="target" />
          <Field name="condition" />
        </Group>
      </EdgeResource>
    </div>
  );
}

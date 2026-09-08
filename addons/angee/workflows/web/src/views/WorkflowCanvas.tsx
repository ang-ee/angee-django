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
  canonicalOptionValue,
  useCollapsiblePane,
  useContainerQuery,
  useEnumOptions,
  useImplConfigFields,
  useImplPrefill,
  type GraphViewConnection,
  type GraphViewPosition,
} from "@angee/ui";

import {
  CreateWorkflowEdgeDocument,
  UpdateWorkflowStepPositionDocument,
  WorkflowGraphDocument,
  WorkflowStepOperationsDocument,
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
  const selectedStepRecord = steps.find((step) => step.id === selectedStep);
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
        <CanvasInspector
          selectedStep={selectedStepRecord
            ? { id: selectedStepRecord.id, stepClass: selectedStepRecord.step_class, configErrors: configErrors(selectedStepRecord.config_errors) }
            : null}
          selectedEdge={selectedEdge}
          readOnly={!isDraft}
          onChanged={refreshGraph}
        />
      </SplitPane>
    </SplitPanes>
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function CanvasInspector({ selectedStep, selectedEdge, readOnly, onChanged }: { selectedStep: { id: string; stepClass: string; configErrors: Record<string, string[]> } | null; selectedEdge: string | null; readOnly: boolean; onChanged: () => void }): React.ReactElement {
  const t = useWorkflowsT();
  if (selectedStep) return <StepConfigPanel stepId={selectedStep.id} persistedStepClass={selectedStep.stepClass} configErrors={selectedStep.configErrors} readOnly={readOnly} onChanged={onChanged} />;
  if (selectedEdge) return <EdgeConfigPanel edgeId={selectedEdge} readOnly={readOnly} onChanged={onChanged} />;
  return <div className="min-h-0 overflow-auto bg-sheet-1 p-4"><EmptyState icon="workflow-step" title={t("canvas.stepConfig")} description={t("canvas.selectStep")} /></div>;
}

function StepConfigPanel({ stepId, persistedStepClass, configErrors, readOnly, onChanged }: { stepId: string; persistedStepClass: string; configErrors: Record<string, string[]>; readOnly: boolean; onChanged: () => void }): React.ReactElement {
  const t = useWorkflowsT();
  const operationsQuery = useAuthoredQuery(WorkflowStepOperationsDocument);
  const operations = operationsQuery.data?.workflow_step_operations ?? [];
  const stepClassOptions = React.useMemo(() => operationOptions(operations), [operations]);
  const stepClassPrefill = useImplPrefill(STEP_MODEL, "step_class", {}, operations);
  const implConfig = useImplConfigFields(STEP_MODEL, "step_class", operations);
  const joinRuleOptions = useEnumOptions(STEP_MODEL, "join_rule");
  const StepResource = readOnly ? ResourceShow : ResourceEdit;
  const hasConfigErrors = Object.keys(configErrors).length > 0;
  const invalidPersistedConfig = (values: Record<string, unknown>): boolean => (
    hasConfigErrors && String(values.step_class) === persistedStepClass
  );
  return (
    <div className="min-h-0 overflow-auto bg-sheet-1">
      <ErrorBanner description={operationsQuery.error ? errorMessage(operationsQuery.error) : null} />
      <ErrorBanner description={hasConfigErrors ? configErrorMessage(configErrors) : null} />
      <StepResource resource={STEP_MODEL} id={stepId} onSaved={onChanged}>
        <Field name="name" title />
        <Group label={t("canvas.step")} columns={2}>
          <Field name="workflow" readOnly />
          <Field name="key" />
          <Field
            name="step_class"
            label={t("canvas.operation")}
            widget="select"
            options={stepClassOptions}
            prefill={stepClassPrefill}
            prefillPreserveDirty
            prefillReplace={["config"]}
            resolve={(values) => {
              const options = operationOptions(operations, values.step_class);
              const currentKey = canonicalOptionValue(options, values.step_class);
              return {
                name: "step_class",
                label: t("canvas.operation"),
                widget: "select",
                options,
                description: operationHint(
                  operations.find((operation) => operation.key === currentKey),
                  t,
                ),
              };
            }}
          />
          <Field name="join_rule" widget="select" options={joinRuleOptions} />
          <Field name="is_entry" />
        </Group>
        <Group label={t("canvas.configuration")} columns={1}>
          <Field
            name="config"
            label={t("canvas.rawConfiguration")}
            widget="json"
            showWhen={(values) => !implConfig.hasSchema(values.step_class) || invalidPersistedConfig(values)}
          />
          {implConfig.fields.map((field) => (
            <Field
              key={field.name}
              {...field}
              showWhen={(values) => !invalidPersistedConfig(values) && (field.showWhen?.(values) ?? true)}
              resolve={(values) => {
                const resolved = field.resolve?.(values) ?? field;
                return {
                  ...resolved,
                  showWhen: (current) => (
                    !invalidPersistedConfig(current) && (resolved.showWhen?.(current) ?? true)
                  ),
                };
              }}
            />
          ))}
        </Group>
      </StepResource>
    </div>
  );
}

function configErrors(value: unknown): Record<string, string[]> {
  if (value == null || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).flatMap(([path, messages]) => (
      Array.isArray(messages) && messages.every((message) => typeof message === "string")
        ? [[path, messages]]
        : []
    )),
  );
}

function configErrorMessage(errors: Record<string, string[]>): string {
  return Object.entries(errors)
    .flatMap(([path, messages]) => messages.map((message) => `${path}: ${message}`))
    .join(" ");
}

function operationOptions(
  operations: readonly { key: string; label: string; selectable: boolean }[],
  current?: unknown,
): { value: string; label: string; disabled: boolean }[] {
  const options = operations.map((operation) => ({
    value: operation.key,
    label: operation.label,
    disabled: !operation.selectable,
  }));
  const currentKey = canonicalOptionValue(options, current);
  return options.filter((option) => !option.disabled || option.value === currentKey);
}

type WorkflowT = ReturnType<typeof useWorkflowsT>;

function operationHint(
  operation: { description: string; effect: string; effect_description: string } | undefined,
  t: WorkflowT,
): string {
  if (!operation) return t("canvas.operationUnavailable");
  const effect = operationEffectLabel(operation.effect, t);
  return [operation.description, t("canvas.effectHint", { effect }), operation.effect_description]
    .filter(Boolean)
    .join(" ");
}

function operationEffectLabel(effect: string, t: WorkflowT): string {
  switch (effect) {
    case "NONE": return t("canvas.effect.none");
    case "READ": return t("canvas.effect.read");
    case "WRITE": return t("canvas.effect.write");
    case "EXTERNAL": return t("canvas.effect.external");
    default: return t("canvas.effect.unknown");
  }
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

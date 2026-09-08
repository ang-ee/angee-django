import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Badge,
  BoundDescriptorField,
  Button,
  EmptyState,
  ErrorBanner,
  GraphView,
  Glyph,
  SplitPane,
  SplitPaneHandle,
  SplitPanes,
  canonicalOptionValue,
  useCollapsiblePane,
  useContainerQuery,
  useEnumOptions,
  useImplConfigFields,
  useImplPrefill,
  useFormViewValues,
  type GraphViewConnection,
  type GraphViewEdge,
  type GraphViewNode,
  type GraphViewPosition,
  type RecordPanelContext,
} from "@angee/ui";

import { WorkflowStepOperationsDocument } from "../documents.console";
import { useWorkflowsT } from "../i18n";
import { workflowNodeStyles, type WorkflowGraphNodeKind } from "./graph-data";
import { EMPTY_WORKFLOW_EDITOR_SELECTION, workflowEditorSelection } from "./workflow-editor-state";
import type { DefinitionEdge, DefinitionNode, WorkflowDefinitionValues } from "./workflow-definition-state";

const STEP_MODEL = "workflows.Step";
const EDGE_MODEL = "workflows.Edge";

export function WorkflowCanvas({ context }: { context: RecordPanelContext }): React.ReactElement {
  const t = useWorkflowsT();
  const values = useFormViewValues(context.form) as WorkflowDefinitionValues;
  const nodes = values.definition?.nodes ?? {};
  const edges = values.definition?.edges ?? {};
  const [selection, dispatchSelection] = React.useReducer(workflowEditorSelection, EMPTY_WORKFLOW_EDITOR_SELECTION);
  const selectedStep = selection.stepId;
  const selectedEdge = selection.edgeId;
  const showingInspector = selection.narrowView === "inspector";
  const [containerRef, wide] = useContainerQuery(760);
  const graphPane = useCollapsiblePane();
  const inspectorPane = useCollapsiblePane({ defaultCollapsed: true });
  const hasSelection = selectedStep !== null || selectedEdge !== null;
  const readOnly = context.form.formReadOnly;
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
  const graphNodes = React.useMemo(() => Object.entries(nodes).map(([identity, node]) => ({
    id: identity,
    kind: nodeKind(node.step_class),
    title: node.name || node.key,
    code: node.key,
    detail: node.join_rule,
    highlighted: node.is_entry,
    position: graphPosition(node.position),
    meta: { node },
  })) satisfies GraphViewNode<WorkflowGraphNodeKind, { node: DefinitionNode }>[], [nodes]);
  const graphEdges = React.useMemo(() => Object.entries(edges).map(([identity, edge]) => ({
    id: identity,
    source: edge.source,
    target: edge.target,
    kind: edge.condition ? "condition" as const : "default" as const,
    label: edge.condition || undefined,
    meta: { edge },
  })) satisfies GraphViewEdge<"condition" | "default", { edge: DefinitionEdge }>[], [edges]);

  const handleNodeDragEnd = React.useCallback((node: GraphViewNode<WorkflowGraphNodeKind, { node: DefinitionNode }>, position: GraphViewPosition) => {
    context.form.form.setValue(`definition.nodes.${node.id}.position`, position as never, {
      shouldDirty: true, shouldTouch: true,
    });
  }, [context.form.form]);
  const handleConnect = React.useCallback((edge: GraphViewConnection) => {
    if (edge.source === edge.target) return;
    const clientKey = `edge-${crypto.randomUUID()}`;
    context.form.form.setValue(`definition.edges.${clientKey}`, {
      id: "", clientKey, source: edge.source, target: edge.target, condition: "",
    } as never, { shouldDirty: true, shouldTouch: true });
    dispatchSelection({ type: "select-edge", id: clientKey });
  }, [context.form.form]);

  return (
    <SplitPanes ref={containerRef} autoSave="workflows.canvas" persistLayout={wide} panelIds={["graph", "inspector"]} className="h-full min-h-0 bg-canvas">
      <SplitPane id="graph" defaultSize={68} minSize={42} collapsible panelRef={graphPane.panelRef} onResize={graphPane.onResize} className={!wide && showingInspector ? "hidden" : "relative"}>
        {graphNodes.length === 0 ? (
          <EmptyState fill icon="workflow-canvas" title={t("canvas.emptyTitle")} description={t("canvas.emptyDescription")} />
        ) : (
          <GraphView
            className="h-full"
            nodes={graphNodes}
            edges={graphEdges}
            nodeStyles={workflowNodeStyles}
            nodesDraggable={!readOnly}
            onNodeDragEnd={!readOnly ? handleNodeDragEnd : undefined}
            onConnect={!readOnly ? handleConnect : undefined}
            onNodeClick={(node) => dispatchSelection({ type: "select-step", id: node.id })}
            onEdgeClick={(edge) => dispatchSelection({ type: "select-edge", id: edge.id })}
            onNodeSelect={(node) => { if (node) dispatchSelection({ type: "select-step", id: node.id }); }}
            onEdgeSelect={(edge) => { if (edge) dispatchSelection({ type: "select-edge", id: edge.id }); }}
          />
        )}
        <div className="absolute left-3 top-3"><Badge tone={readOnly ? "neutral" : "warning"}>{readOnly ? t("canvas.readOnlyVersion", { version: values.version ?? "?" }) : t("canvas.draft")}</Badge></div>
      </SplitPane>
      <SplitPaneHandle className={!wide ? "hidden" : undefined} />
      <SplitPane id="inspector" defaultSize={32} minSize={22} collapsible panelRef={inspectorPane.panelRef} onResize={inspectorPane.onResize} className={!wide && !showingInspector ? "hidden" : undefined}>
        {!wide && hasSelection ? <Button type="button" variant="ghost" size="sm" onClick={() => dispatchSelection({ type: "show-canvas" })}><Glyph name="chevron-left" />{t("canvas.back")}</Button> : null}
        <CanvasInspector context={context} nodeKey={selectedStep} edgeKey={selectedEdge} node={selectedStep ? nodes[selectedStep] : undefined} edge={selectedEdge ? edges[selectedEdge] : undefined} nodes={nodes} diagnostics={values.definition?.readiness ?? []} />
      </SplitPane>
    </SplitPanes>
  );
}

function CanvasInspector({ context, nodeKey, edgeKey, node, edge, nodes, diagnostics }: { context: RecordPanelContext; nodeKey: string | null; edgeKey: string | null; node?: DefinitionNode; edge?: DefinitionEdge; nodes: Record<string, DefinitionNode>; diagnostics: WorkflowDefinitionValues["definition"]["readiness"] }): React.ReactElement {
  const t = useWorkflowsT();
  if (nodeKey && node) return <StepConfigPanel context={context} nodeKey={nodeKey} node={node} diagnostics={diagnostics} />;
  if (edgeKey && edge) return <EdgeConfigPanel context={context} edgeKey={edgeKey} edge={edge} nodes={nodes} diagnostics={diagnostics} />;
  return <div className="min-h-0 overflow-auto bg-sheet-1 p-4"><EmptyState icon="workflow-step" title={t("canvas.stepConfig")} description={t("canvas.selectStep")} /></div>;
}

function StepConfigPanel({ context, nodeKey, node, diagnostics }: { context: RecordPanelContext; nodeKey: string; node: DefinitionNode; diagnostics: WorkflowDefinitionValues["definition"]["readiness"] }): React.ReactElement {
  const t = useWorkflowsT();
  const operationsQuery = useAuthoredQuery(WorkflowStepOperationsDocument);
  const operations = operationsQuery.data?.workflow_step_operations ?? [];
  const options = React.useMemo(() => operationOptions(operations, node.step_class), [node.step_class, operations]);
  const prefill = useImplPrefill(STEP_MODEL, "step_class", {}, operations);
  const implConfig = useImplConfigFields(STEP_MODEL, "step_class", operations);
  const joinRuleOptions = useEnumOptions(STEP_MODEL, "join_rule");
  const scope = `definition.nodes.${nodeKey}`;
  const invalidConfig = node.config == null || typeof node.config !== "object" || Array.isArray(node.config);
  const nodeErrors = [configErrorMessage(node.config_errors), ...diagnostics.filter((item) => String(item.kind).toUpperCase() === "NODE" && (item.id === node.id || item.client_key === nodeKey)).map((item) => item.message)].filter(Boolean).join(" ");
  return <div className="grid min-h-0 gap-4 overflow-auto bg-sheet-1 p-4">
    <ErrorBanner description={operationsQuery.error ? errorMessage(operationsQuery.error) : null} />
    <ErrorBanner description={nodeErrors || null} />
    <BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{ name: "name" }} />
    <BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{ name: "key" }} />
    <BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{
      name: "step_class", label: t("canvas.operation"), widget: "select", options, prefill,
      prefillPreserveDirty: true, prefillReplace: ["config"],
      resolve: (values) => {
        const currentOptions = operationOptions(operations, values.step_class);
        const key = canonicalOptionValue(currentOptions, values.step_class);
        return { name: "step_class", widget: "select", options: currentOptions, description: operationHint(operations.find((operation) => operation.key === key), t) };
      },
    }} />
    {implConfig.fields.map((field) => <BoundDescriptorField key={field.name} form={context.form} resource={STEP_MODEL} scope={scope} field={{ ...field, readOnly: invalidConfig || field.readOnly }} />)}
    {invalidConfig || !implConfig.hasSchema(node.step_class) ? <BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{ name: "config", label: t("canvas.rawConfiguration"), widget: "json" }} /> : null}
    <BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{ name: "join_rule", widget: "select", options: joinRuleOptions }} />
    <BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{ name: "is_entry" }} />
  </div>;
}

function EdgeConfigPanel({ context, edgeKey, edge, nodes, diagnostics }: { context: RecordPanelContext; edgeKey: string; edge: DefinitionEdge; nodes: Record<string, DefinitionNode>; diagnostics: WorkflowDefinitionValues["definition"]["readiness"] }): React.ReactElement {
  const t = useWorkflowsT();
  const scope = `definition.edges.${edgeKey}`;
  const options = Object.entries(nodes).map(([value, node]) => ({ value, label: node.name || node.key }));
  const edgeErrors = diagnostics.filter((item) => String(item.kind).toUpperCase() === "EDGE" && (item.id === edge.id || item.client_key === edgeKey)).map((item) => item.message).join(" ");
  return <div className="grid min-h-0 gap-4 overflow-auto bg-sheet-1 p-4">
    <ErrorBanner description={edgeErrors || null} />
    <BoundDescriptorField form={context.form} resource={EDGE_MODEL} scope={scope} field={{ name: "source", label: t("canvas.source"), widget: "select", options }} />
    <BoundDescriptorField form={context.form} resource={EDGE_MODEL} scope={scope} field={{ name: "target", label: t("canvas.target"), widget: "select", options }} />
    <BoundDescriptorField form={context.form} resource={EDGE_MODEL} scope={scope} field={{ name: "condition", label: t("canvas.condition") }} />
  </div>;
}

function nodeKind(value: string): WorkflowGraphNodeKind {
  const normalized = value.toUpperCase();
  return normalized in workflowNodeStyles ? normalized as WorkflowGraphNodeKind : "HANDLER";
}
function graphPosition(value: unknown): GraphViewPosition | undefined {
  if (!value || typeof value !== "object") return undefined;
  const position = value as { x?: unknown; y?: unknown };
  return typeof position.x === "number" && typeof position.y === "number" ? { x: position.x, y: position.y } : undefined;
}
function errorMessage(error: unknown): string { return error instanceof Error ? error.message : String(error); }
function configErrorMessage(value: unknown): string {
  if (value == null || typeof value !== "object" || Array.isArray(value)) return "";
  return Object.entries(value).flatMap(([path, messages]) => Array.isArray(messages) ? messages.map((message) => `${path}: ${String(message)}`) : []).join(" ");
}
function operationOptions(operations: readonly { key: string; label: string; selectable: boolean }[], current?: unknown): { value: string; label: string; disabled: boolean }[] {
  const options = operations.map((operation) => ({ value: operation.key, label: operation.label, disabled: !operation.selectable }));
  const currentKey = canonicalOptionValue(options, current);
  return options.filter((option) => !option.disabled || option.value === currentKey);
}
type WorkflowT = ReturnType<typeof useWorkflowsT>;
function operationHint(operation: { description: string; effect: string; effect_description: string } | undefined, t: WorkflowT): string {
  if (!operation) return t("canvas.operationUnavailable");
  return [operation.description, t("canvas.effectHint", { effect: operationEffectLabel(operation.effect, t) }), operation.effect_description].filter(Boolean).join(" ");
}
function operationEffectLabel(effect: string, t: WorkflowT): string {
  switch (effect) { case "NONE": return t("canvas.effect.none"); case "READ": return t("canvas.effect.read"); case "WRITE": return t("canvas.effect.write"); case "EXTERNAL": return t("canvas.effect.external"); default: return t("canvas.effect.unknown"); }
}

import * as React from "react";
import { useModelMetadata } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";
import {
  Badge,
  BoundDescriptorField,
  BoundFormValue,
  Button,
  CollapsibleIcon,
  CollapsiblePanel,
  CollapsibleRoot,
  CollapsibleTrigger,
  EmptyState,
  errorMessage,
  ErrorBanner,
  FieldDescriptorControl,
  GraphView,
  Glyph,
  MutationDialog,
  SplitPane,
  SplitPaneHandle,
  SplitPanes,
  Tabs,
  canonicalOptionValue,
  slugify,
  useCollapsiblePane,
  useContainerQuery,
  useEnumOptions,
  useImplConfigFields,
  useImplPrefill,
  useFormViewValues,
  type GraphViewConnection,
  type GraphViewEdge,
  type GraphViewGeometry,
  type GraphViewNode,
  type GraphViewPosition,
  type FieldDescriptor,
  type RecordPanelContext,
} from "@angee/ui";
import { fieldsWithMetadataDefaults } from "@angee/ui/views/model-metadata-defaults";

import { WorkflowMapBodyCandidatesDocument, WorkflowStepOperationsDocument } from "../documents.console";
import { useWorkflowsT } from "../i18n";
import { WorkflowOperationPicker, type WorkflowOperationChoice } from "./WorkflowOperationPicker";
import { WorkflowInputBindingEditor } from "./WorkflowInputBindingEditor";
import { workflowNodeKind, workflowNodeStyles, type WorkflowGraphNodeKind } from "./graph-data";
import { graphWithDuplicate, graphWithMapBody, graphWithOperation, positionFrom } from "./workflow-graph-authoring";
import { EMPTY_WORKFLOW_EDITOR_SELECTION, workflowEditorSelection } from "./workflow-editor-state";
import type { DefinitionEdge, DefinitionNode, WorkflowDefinitionValues } from "./workflow-definition-state";
import { useDefinitionHistoryContext } from "./workflow-definition-history";
import { useWorkflowTestLaunch } from "./WorkflowTestSetup";
import { useWorkflowInputPreview } from "./workflow-input-preview";

const STEP_MODEL = "workflows.Step";
const EDGE_MODEL = "workflows.Edge";
const WORKFLOW_EDGE_STYLES = {
  map: { stroke: "var(--info)", strokeWidth: 2, labelColor: "var(--info)" },
} as const;

export function WorkflowCanvas({ context }: { context: RecordPanelContext; }): React.ReactElement {
  const t = useWorkflowsT();
  const focusField = context.focusField;
  const definition = useFormViewValues<WorkflowDefinitionValues["definition"]>(context.form, "definition");
  const version = useFormViewValues<WorkflowDefinitionValues["version"]>(context.form, "version");
  const history = useDefinitionHistoryContext();
  const perform = React.useCallback((action: () => void) => history ? history.perform(action) : action(), [history]);
  const nodes = definition?.nodes ?? {};
  const edges = definition?.edges ?? {};
  const [selection, dispatchSelection] = React.useReducer(workflowEditorSelection, EMPTY_WORKFLOW_EDITOR_SELECTION);
  const selectedStep = selection.stepId;
  const selectedEdge = selection.edgeId;
  const showingInspector = selection.narrowView === "inspector";
  const [containerRef, wide] = useContainerQuery(760);
  const graphPane = useCollapsiblePane();
  const inspectorPane = useCollapsiblePane({ defaultCollapsed: true });
  const hasSelection = selectedStep !== null || selectedEdge !== null;
  const readOnly = context.form.formReadOnly;
  const operationsQuery = useAuthoredQuery(WorkflowStepOperationsDocument);
  const operations = operationsQuery.data?.workflow_step_operations ?? [];
  const declaredFields = useWorkflowCanvasFields(t, operations);
  const graphGeometry = React.useRef<GraphViewGeometry<WorkflowGraphNodeKind>>(null);
  const graphSurface = React.useRef<HTMLDivElement>(null);
  const suppressDefaultInspectorFocus = React.useRef(false);
  const keyboardFocusSequence = React.useRef(0);
  const [keyboardFocus, setKeyboardFocus] = React.useState<{
    kind: "node" | "edge" | "map";
    id: string;
    sequence: number;
  } | null>(null);
  const [canvasFocusRequest, requestCanvasFocus] = React.useReducer((request) => request + 1, 0);
  const diagnostics = definition?.readiness ?? [];
  const [pendingIssue, setPendingIssue] = React.useState<DefinitionDiagnostic | null>(null);
  const [issuesOpen, setIssuesOpen] = React.useState(false);
  const [palette, setPalette] = React.useState<{ kind: "first" | "after" | "insert" | "map-body"; identity?: string; } | null>(null);
  const [connectOpen, setConnectOpen] = React.useState(false);
  React.useEffect(() => {
    if ((selectedStep && !Object.hasOwn(nodes, selectedStep)) || (selectedEdge && !Object.hasOwn(edges, selectedEdge))) {
      setKeyboardFocus(null);
      dispatchSelection({ type: "clear" });
      if (!wide) requestCanvasFocus();
    }
  }, [edges, nodes, selectedEdge, selectedStep, wide]);
  React.useEffect(() => {
    if (!keyboardFocus) return;
    if (keyboardFocus.kind === "node" && selectedStep === keyboardFocus.id) {
      focusField(`definition.nodes.${keyboardFocus.id}.name`);
    } else if (keyboardFocus.kind === "edge" && selectedEdge === keyboardFocus.id) {
      focusField(`definition.edges.${keyboardFocus.id}.condition`);
    } else if (keyboardFocus.kind === "map" && selectedStep === keyboardFocus.id) {
      focusField(`definition.nodes.${keyboardFocus.id}.config.target_step`);
    } else {
      return;
    }
    setKeyboardFocus((current) => current?.sequence === keyboardFocus.sequence ? null : current);
  }, [focusField, keyboardFocus, selectedEdge, selectedStep]);
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
  React.useEffect(() => {
    if (wide || !showingInspector) return;
    if (suppressDefaultInspectorFocus.current) {
      suppressDefaultInspectorFocus.current = false;
      return;
    }
    if (selectedStep) focusField(`definition.nodes.${selectedStep}.name`);
    else if (selectedEdge) focusField(`definition.edges.${selectedEdge}.condition`);
  }, [focusField, selectedEdge, selectedStep, showingInspector, wide]);
  React.useLayoutEffect(() => {
    if (canvasFocusRequest > 0 && !wide && !showingInspector) graphSurface.current?.focus();
  }, [canvasFocusRequest, showingInspector, wide]);
  const mapOwnersByTarget = React.useMemo(() => {
    const byKey = new Map<string, string>();
    for (const [identity, node] of Object.entries(nodes)) {
      byKey.set(node.key, byKey.has(node.key) ? "" : identity);
    }
    return new Map(Object.entries(nodes).flatMap(([ownerIdentity, owner]) => {
      const operation = operationChoice(operations, owner.step_class);
      const config = jsonObject(owner.config);
      const targetKey = operation?.map_body_operation && typeof config.target_step === "string" ? config.target_step : "";
      if (!targetKey) return [];
      const targetIdentity = byKey.get(targetKey);
      return targetIdentity ? [[targetIdentity, ownerIdentity] as const] : [];
    }));
  }, [nodes, operations]);
  const graphNodes = React.useMemo(() => Object.entries(nodes).map(([identity, node]) => {
    const operation = operationChoice(operations, node.step_class);
    const title = node.name || operation?.label || node.key;
    const issueCount = diagnosticsForNode(diagnostics, identity, node).length;
    const mapOwner = mapOwnersByTarget.get(identity);
    const mapOwnerLabel = mapOwner ? t("map.bodyOf", { source: nodes[mapOwner]?.name || nodes[mapOwner]?.key || mapOwner }) : null;
    const secondary = node.name && operation?.label && node.name !== operation.label ? operation.label : null;
    return {
      id: identity,
      kind: workflowNodeKind(node.step_class),
      kindLabel: operation?.category || t("canvas.operationUnavailableShort"),
      ariaLabel: [title, secondary, operation?.category, mapOwnerLabel, node.is_entry ? t("canvas.start") : null, issueCount ? issueLabel(issueCount, t) : null].filter(Boolean).join(", "),
      title,
      detail: [secondary, mapOwnerLabel, node.is_entry ? t("canvas.start") : null, issueCount ? issueLabel(issueCount, t) : null].filter(Boolean).join(" · "),
      selected: identity === selectedStep,
      position: positionFrom(node.position),
      meta: { node },
    };
  }) satisfies GraphViewNode<WorkflowGraphNodeKind, { node: DefinitionNode; }>[], [diagnostics, mapOwnersByTarget, nodes, operations, selectedStep, t]);
  const graphEdges = React.useMemo<GraphViewEdge<"condition" | "default" | "map", WorkflowCanvasEdgeMeta>[]>(() => {
    const ordinary = Object.entries(edges).map(([identity, edge]) => {
    const outcome = outcomeChoiceForEdge(edge, nodes, operations);
    const outcomeLabel = outcome?.label ?? edge.condition;
    return {
      id: identity,
      source: edge.source,
      target: edge.target,
      kind: edge.condition ? "condition" as const : "default" as const,
      label: outcomeLabel || undefined,
      selected: identity === selectedEdge,
      ariaLabel: edgeAriaLabel(edge, nodes, outcomeLabel, diagnosticsForEdge(diagnostics, identity, edge).length, t),
      meta: { edge, mapOwner: null },
    };
    });
    const byKey = new Map<string, string>();
    for (const [identity, node] of Object.entries(nodes)) {
      if (node.key && !byKey.has(node.key)) byKey.set(node.key, identity);
      else if (node.key) byKey.set(node.key, "");
    }
    const mapped = Object.entries(nodes).flatMap(([identity, node]) => {
      const operation = operationChoice(operations, node.step_class);
      const config = jsonObject(node.config);
      const targetKey = operation?.map_body_operation && typeof config.target_step === "string"
        ? config.target_step
        : "";
      const target = byKey.get(targetKey);
      if (!target) return [];
      return [{
        id: `map-body:${identity}`,
        source: identity,
        target,
        kind: "map" as const,
        label: t("map.eachItem"),
        ariaLabel: t("map.eachItemAria", { source: node.name || node.key, target: nodes[target]?.name || targetKey }),
        meta: { edge: null, mapOwner: identity },
      }];
    });
    return [...ordinary, ...mapped];
  }, [diagnostics, edges, nodes, operations, selectedEdge, t]);
  const currentGraph = React.useCallback(() => ({
    nodes: (context.form.form.getValues("definition.nodes") ?? {}) as Record<string, DefinitionNode>,
    edges: (context.form.form.getValues("definition.edges") ?? {}) as Record<string, DefinitionEdge>,
  }), [context.form.form]);

  const handleNodeDragEnd = React.useCallback((node: GraphViewNode<WorkflowGraphNodeKind, { node: DefinitionNode; }>, position: GraphViewPosition) => {
    perform(() => context.form.form.setValue(`definition.nodes.${node.id}.position`, position as never, {
      shouldDirty: true, shouldTouch: true,
    }));
  }, [context.form.form, perform]);
  const handleConnect = React.useCallback((edge: GraphViewConnection & { condition?: string; }) => {
    const latest = currentGraph();
    if (readOnly || edge.source === edge.target || !latest.nodes[edge.source] || !latest.nodes[edge.target]) return;
    const clientKey = `edge-${crypto.randomUUID()}`;
    perform(() => context.form.form.setValue(`definition.edges.${clientKey}`, {
      id: "", clientKey, source: edge.source, target: edge.target, condition: edge.condition ?? "",
    } as never, { shouldDirty: true, shouldTouch: true }));
    dispatchSelection({ type: "select-edge", id: clientKey });
  }, [context.form.form, currentGraph, perform, readOnly]);
  const chooseOperation = React.useCallback((operationKey: string) => {
    if (!palette || readOnly) return;
    const latest = currentGraph();
    const operation = operations.find((choice) => choice.key === operationKey);
    if (!operation?.selectable) return;
    if (palette.kind === "map-body" && operation.map_body_operation) return;
    if (palette.identity && palette.kind === "after" && !latest.nodes[palette.identity]) return;
    if (palette.identity && palette.kind === "insert" && !latest.edges[palette.identity]) return;
    const clientKey = `node-${crypto.randomUUID()}`;
    const defaults = jsonObject(operation.defaults);
    const defaultName = typeof defaults.name === "string" ? defaults.name : operation.label;
    const defaultKey = typeof defaults.key === "string" ? defaults.key : defaultName;
    const node: DefinitionNode = {
      id: "", clientKey, key: uniqueNodeKey(defaultKey || operation.key, latest.nodes), name: defaultName,
      step_class: operation.key, config: jsonObject(defaults.config), config_errors: {}, join_rule: typeof defaults.join_rule === "string" ? defaults.join_rule : "ALL_SUCCESS",
      input_binding: null, is_entry: Object.keys(latest.nodes).length === 0, position: {},
    };
    const updated = palette.kind === "map-body" && palette.identity
      ? graphWithMapBody(node, palette.identity, latest.nodes, latest.edges, graphGeometry.current)
      : graphWithOperation(node, palette as { kind: "first" | "after" | "insert"; identity?: string }, latest.nodes, latest.edges, graphGeometry.current);
    if (!updated) return;
    perform(() => {
      context.form.form.setValue("definition.nodes", updated.nodes as never, { shouldDirty: true, shouldTouch: true });
      context.form.form.setValue("definition.edges", updated.edges as never, { shouldDirty: true, shouldTouch: true });
    });
    dispatchSelection({ type: "select-step", id: clientKey });
  }, [context.form.form, currentGraph, operations, palette, perform, readOnly]);
  const deleteNode = React.useCallback((identity: string) => {
    const latest = currentGraph();
    if (readOnly || !latest.nodes[identity]) return;
    const { [identity]: _removed, ...remainingNodes } = latest.nodes;
    const remainingEdges = Object.fromEntries(Object.entries(latest.edges).filter(([, edge]) => edge.source !== identity && edge.target !== identity));
    perform(() => {
      context.form.form.setValue("definition.nodes", remainingNodes as never, { shouldDirty: true, shouldTouch: true });
      context.form.form.setValue("definition.edges", remainingEdges as never, { shouldDirty: true, shouldTouch: true });
    });
    dispatchSelection({ type: "clear" });
  }, [context.form.form, currentGraph, perform, readOnly]);
  const duplicateNode = React.useCallback((identity: string) => {
    if (readOnly) return;
    const latest = currentGraph();
    const source = latest.nodes[identity]; if (!source) return;
    const clientKey = `node-${crypto.randomUUID()}`;
    const copy = { ...structuredClone(source), id: "", clientKey, key: uniqueNodeKey(`${source.key}-copy`, latest.nodes), name: `${source.name} copy`, config_errors: {}, is_entry: false, position: {} };
    const updated = graphWithDuplicate(copy, identity, latest.nodes, graphGeometry.current);
    perform(() => context.form.form.setValue("definition.nodes", updated as never, { shouldDirty: true, shouldTouch: true }));
    dispatchSelection({ type: "select-step", id: clientKey });
  }, [context.form.form, currentGraph, perform, readOnly]);
  const makeEntry = React.useCallback((identity: string) => {
    const latest = currentGraph();
    if (readOnly || !latest.nodes[identity]) return;
    perform(() => context.form.form.setValue("definition.nodes", Object.fromEntries(Object.entries(latest.nodes).map(([key, node]) => [key, { ...node, is_entry: key === identity }])) as never, { shouldDirty: true, shouldTouch: true }));
  }, [context.form.form, currentGraph, perform, readOnly]);
  const deleteEdge = React.useCallback((identity: string) => {
    const latest = currentGraph();
    if (readOnly || !latest.edges[identity]) return;
    const { [identity]: _removed, ...remainingEdges } = latest.edges;
    perform(() => context.form.form.setValue("definition.edges", remainingEdges as never, { shouldDirty: true, shouldTouch: true }));
    dispatchSelection({ type: "clear" });
  }, [context.form.form, currentGraph, perform, readOnly]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-canvas">
      {diagnostics.length ? <CollapsibleRoot open={issuesOpen} onOpenChange={setIssuesOpen} variant="section" className="shrink-0 border-b border-border-subtle bg-sheet-1 px-3 py-1">
        <CollapsibleTrigger><CollapsibleIcon />{issueLabel(diagnostics.length, t)}</CollapsibleTrigger>
        <CollapsiblePanel><div className="grid max-h-40 gap-1 overflow-auto pb-2" aria-label={t("canvas.issues")}><span className="px-2 text-12 text-fg-muted">{t("canvas.unsavedIssuesHint")}</span>{diagnostics.map((diagnostic, index) => <Button key={`${diagnostic.code}-${diagnostic.kind}-${diagnostic.id ?? diagnostic.client_key ?? "unknown"}-${diagnostic.field}-${index}`} type="button" size="sm" variant="ghost" className="h-auto min-w-0 justify-start whitespace-normal text-left" onClick={() => {
          const kind = String(diagnostic.kind).toUpperCase();
          if (kind === "NODE") {
            const identity = diagnosticIdentity(diagnostic, nodes); if (!identity) return;
            suppressDefaultInspectorFocus.current = true; setPendingIssue(diagnostic); dispatchSelection({ type: "select-step", id: identity });
          } else if (kind === "EDGE") {
            const identity = diagnosticIdentity(diagnostic, edges); if (!identity) return;
            suppressDefaultInspectorFocus.current = true; setPendingIssue(diagnostic); dispatchSelection({ type: "select-edge", id: identity });
          } else {
            context.focusField(diagnostic.field, { recordTabId: "overview" });
          }
          if (!wide) setIssuesOpen(false);
        }}>{diagnosticLabel(diagnostic, nodes, edges, declaredFields, t)}</Button>)}</div></CollapsiblePanel>
      </CollapsibleRoot> : null}
      <SplitPanes ref={containerRef} autoSave="workflows.canvas" persistLayout={wide} panelIds={["graph", "inspector"]} className="min-h-0 flex-1 bg-canvas">
        <SplitPane id="graph" defaultSize={68} minSize={42} collapsible panelRef={graphPane.panelRef} onResize={graphPane.onResize} className={!wide && showingInspector ? "hidden" : "relative"}>
          {graphNodes.length === 0 ? (
            <EmptyState fill icon="workflow-canvas" title={t("canvas.emptyTitle")} description={t("canvas.emptyDescription")} actions={!readOnly ? <Button type="button" onClick={() => setPalette({ kind: "first" })}>{t("canvas.addFirst")}</Button> : null} />
          ) : (
            <GraphView
              className="h-full"
              ariaLabel={t("tabs.editor")}
              fitViewOptions={{ padding: 0.18, maxZoom: 1 }}
              geometryRef={graphGeometry}
              surfaceRef={graphSurface}
              nodes={graphNodes}
              edges={graphEdges}
              nodeStyles={workflowNodeStyles}
              nodesDraggable={!readOnly}
              onNodeDragEnd={!readOnly ? handleNodeDragEnd : undefined}
              onConnect={!readOnly ? handleConnect : undefined}
              onNodeClick={(node, activation) => {
                if (activation.source === "keyboard") {
                  setKeyboardFocus({ kind: "node", id: node.id, sequence: ++keyboardFocusSequence.current });
                }
                dispatchSelection({ type: "select-step", id: node.id });
              }}
              edgeStyles={WORKFLOW_EDGE_STYLES}
              onEdgeClick={(edge, activation) => {
                if (edge.meta?.mapOwner) {
                  if (activation.source === "keyboard") {
                    setKeyboardFocus({ kind: "map", id: edge.meta.mapOwner, sequence: ++keyboardFocusSequence.current });
                  }
                  dispatchSelection({ type: "select-step", id: edge.meta.mapOwner });
                  return;
                }
                if (activation.source === "keyboard") {
                  setKeyboardFocus({ kind: "edge", id: edge.id, sequence: ++keyboardFocusSequence.current });
                }
                dispatchSelection({ type: "select-edge", id: edge.id });
              }}
              onNodeSelect={(node) => { if (node) dispatchSelection({ type: "select-step", id: node.id }); }}
              onEdgeSelect={(edge) => {
                if (edge?.meta?.mapOwner) dispatchSelection({ type: "select-step", id: edge.meta.mapOwner });
                else if (edge) dispatchSelection({ type: "select-edge", id: edge.id });
              }}
            />
          )}
          <div className="absolute left-3 top-3"><Badge tone={readOnly ? "neutral" : "warning"}>{readOnly ? t("canvas.readOnlyVersion", { version: version ?? "?" }) : t("canvas.draft")}</Badge></div>
          {!readOnly && graphNodes.length ? <div className="absolute right-3 top-3 flex gap-2"><Button type="button" size="sm" variant="secondary" onClick={() => setConnectOpen(true)}>{t("canvas.connectSteps")}</Button><Button type="button" size="sm" onClick={() => setPalette({ kind: "first" })}>{t("canvas.addStep")}</Button></div> : null}
        </SplitPane>
        <SplitPaneHandle className={!wide ? "hidden" : undefined} />
        <SplitPane id="inspector" defaultSize={32} minSize={22} collapsible panelRef={inspectorPane.panelRef} onResize={inspectorPane.onResize} className={!wide && !showingInspector ? "hidden" : undefined}>
          {!wide && hasSelection ? <Button type="button" variant="ghost" size="sm" onClick={() => { dispatchSelection({ type: "show-canvas" }); requestCanvasFocus(); }}><Glyph name="chevron-left" />{t("canvas.back")}</Button> : null}
        <CanvasInspector context={context} nodeKey={selectedStep} edgeKey={selectedEdge} node={selectedStep ? nodes[selectedStep] : undefined} edge={selectedEdge ? edges[selectedEdge] : undefined} nodes={nodes} operations={operations} diagnostics={diagnostics} pendingIssue={pendingIssue} fields={declaredFields} onIssueFocused={() => setPendingIssue(null)} onSelectStep={(id) => { suppressDefaultInspectorFocus.current = false; dispatchSelection({ type: "select-step", id }); }} onAddAfter={(identity) => setPalette({ kind: "after", identity })} onAddMapBody={(identity) => setPalette({ kind: "map-body", identity })} onInsert={(identity) => setPalette({ kind: "insert", identity })} onDuplicate={duplicateNode} onDelete={deleteNode} onDeleteEdge={deleteEdge} onMakeEntry={makeEntry} />
        </SplitPane>
        <WorkflowOperationPicker operations={(palette?.kind === "map-body"
          ? operations.map((operation) => operation.map_body_operation ? { ...operation, selectable: false } : operation)
          : operations)} open={palette !== null} onOpenChange={(open) => { if (!open) setPalette(null); }} onChoose={chooseOperation} loading={operationsQuery.isFetching} error={operationsQuery.error ? errorMessage(operationsQuery.error, t("canvas.operationUnavailable")) : null} />
        <MutationDialog
          open={connectOpen}
          onOpenChange={setConnectOpen}
          title={t("canvas.connectSteps")}
          description={t("canvas.connectDescription")}
          fields={[
            { name: "source", label: t("canvas.source"), widget: "select", options: nodeOptions(nodes), required: true },
            { name: "target", label: t("canvas.target"), widget: "select", options: nodeOptions(nodes), required: true },
            {
              name: "condition",
              label: t("canvas.condition"),
              resolve: (dialogValues) => outcomeField(
                { name: "condition", label: t("canvas.condition") },
                {
                  condition: String(dialogValues.condition ?? ""),
                  source: String(dialogValues.source ?? ""),
                },
                nodes,
                operations,
                t,
              ),
            },
          ]}
          initialValues={{ source: selectedStep ?? "", target: "", condition: "" }}
          submitLabel={t("canvas.connect")}
          parseValues={(dialogValues) => ({ source: String(dialogValues.source ?? ""), target: String(dialogValues.target ?? ""), condition: String(dialogValues.condition ?? "") })}
          onSubmit={(connection) => {
            if (connection.source === connection.target) throw new Error(t("canvas.connectSelf"));
            handleConnect(connection);
          }}
        />
      </SplitPanes>
    </div>
  );
}

function CanvasInspector({ context, nodeKey, edgeKey, node, edge, nodes, operations, diagnostics, pendingIssue, fields, onIssueFocused, onSelectStep, onAddAfter, onAddMapBody, onInsert, onDuplicate, onDelete, onDeleteEdge, onMakeEntry }: { context: RecordPanelContext; nodeKey: string | null; edgeKey: string | null; node?: DefinitionNode; edge?: DefinitionEdge; nodes: Record<string, DefinitionNode>; operations: readonly WorkflowOperationChoice[]; diagnostics: WorkflowDefinitionValues["definition"]["readiness"]; pendingIssue: DefinitionDiagnostic | null; fields: WorkflowCanvasFields; onIssueFocused: () => void; onSelectStep: (id: string) => void; onAddAfter: (id: string) => void; onAddMapBody: (id: string) => void; onInsert: (id: string) => void; onDuplicate: (id: string) => void; onDelete: (id: string) => void; onDeleteEdge: (id: string) => void; onMakeEntry: (id: string) => void; }): React.ReactElement {
  const t = useWorkflowsT();
  if (nodeKey && node) return <StepConfigPanel context={context} nodeKey={nodeKey} node={node} diagnostics={diagnostics} pendingIssue={pendingIssue} fields={fields} onIssueFocused={onIssueFocused} nodes={nodes} onSelectStep={onSelectStep} onAddAfter={onAddAfter} onAddMapBody={onAddMapBody} onDuplicate={onDuplicate} onDelete={onDelete} onMakeEntry={onMakeEntry} />;
  if (edgeKey && edge) return <EdgeConfigPanel context={context} edgeKey={edgeKey} edge={edge} nodes={nodes} operations={operations} diagnostics={diagnostics} pendingIssue={pendingIssue} fields={fields} onIssueFocused={onIssueFocused} onInsert={onInsert} onDelete={onDeleteEdge} />;
  return <div className="min-h-0 overflow-auto bg-sheet-1 p-4"><EmptyState icon="workflow-step" title={t("canvas.stepConfig")} description={t("canvas.selectStep")} /></div>;
}

function StepConfigPanel({ context, nodeKey, node, nodes, diagnostics, pendingIssue, fields, onIssueFocused, onSelectStep, onAddAfter, onAddMapBody, onDuplicate, onDelete, onMakeEntry }: { context: RecordPanelContext; nodeKey: string; node: DefinitionNode; nodes: Record<string, DefinitionNode>; diagnostics: WorkflowDefinitionValues["definition"]["readiness"]; pendingIssue: DefinitionDiagnostic | null; fields: WorkflowCanvasFields; onIssueFocused: () => void; onSelectStep: (id: string) => void; onAddAfter: (id: string) => void; onAddMapBody: (id: string) => void; onDuplicate: (id: string) => void; onDelete: (id: string) => void; onMakeEntry: (id: string) => void; }): React.ReactElement {
  const t = useWorkflowsT();
  const testStep = useWorkflowTestLaunch();
  const history = useDefinitionHistoryContext();
  const perform = React.useCallback((action: () => void) => history ? history.perform(action) : action(), [history]);
  const operationsQuery = useAuthoredQuery(WorkflowStepOperationsDocument);
  const operations = operationsQuery.data?.workflow_step_operations ?? [];
  const options = React.useMemo(() => operationOptions(operations, node.step_class), [node.step_class, operations]);
  const prefill = useImplPrefill(STEP_MODEL, "step_class", {}, operations);
  const implConfig = fields.implConfig;
  const joinRuleOptions = useEnumOptions(STEP_MODEL, "join_rule");
  const scope = `definition.nodes.${nodeKey}`;
  const invalidConfig = node.config == null || typeof node.config !== "object" || Array.isArray(node.config);
  const [advancedOpen, setAdvancedOpen] = React.useState(false);
  const [tab, setTab] = React.useState("configuration");
  React.useEffect(() => setTab("configuration"), [nodeKey]);
  const issueTarget = pendingIssue && diagnosticIdentity(pendingIssue, { [nodeKey]: node }) === nodeKey
    ? nodeDiagnosticField(pendingIssue.field, implConfig.fields.map((field) => field.name), invalidConfig || !implConfig.hasSchema(node.step_class))
    : null;
  React.useEffect(() => {
    if (issueTarget === "key" || issueTarget === "join_rule") setAdvancedOpen(true);
    if (pendingIssue?.field === "input_binding") setTab("input");
  }, [issueTarget, pendingIssue?.field]);
  React.useEffect(() => {
    if (!issueTarget || ((issueTarget === "key" || issueTarget === "join_rule") && !advancedOpen)) return;
    context.focusField(`${scope}.${issueTarget}`);
    onIssueFocused();
  }, [advancedOpen, context, issueTarget, onIssueFocused, scope]);
  const nodeErrors = diagnosticsForNode(diagnostics, nodeKey, node).filter((item) => item.field !== "input_binding").map((item) => item.message).join(" ");
  const operation = operations.find((item) => item.key === node.step_class);
  const bindingName = `${scope}.input_binding`;
  const inputDiagnostics = diagnosticsForNode(diagnostics, nodeKey, node).filter((item) => item.field === "input_binding");
  return <div className="grid min-h-0 content-start gap-4 overflow-auto bg-sheet-1 p-4">
    <ErrorBanner description={operationsQuery.error ? errorMessage(operationsQuery.error, t("canvas.operationUnavailable")) : null} />
    <ErrorBanner description={nodeErrors || null} />
    <BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={fields.step.name} />
    <BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{
      ...fields.step.operation, widget: "select", options, prefill,
      prefillPreserveDirty: true, prefillReplace: ["config"],
      resolve: (values) => {
        const currentOptions = operationOptions(operations, values.step_class);
        const key = canonicalOptionValue(currentOptions, values.step_class);
        return { name: "step_class", widget: "select", options: currentOptions, description: operationHint(operations.find((operation) => operation.key === key), t) };
      },
    }} />
    <Tabs value={tab} onValueChange={(value) => setTab(String(value))} variant="card">
      <Tabs.List><Tabs.Tab value="configuration">{t("input.configuration")}</Tabs.Tab><Tabs.Tab value="input">{t("input.tab")}</Tabs.Tab></Tabs.List>
      <Tabs.Panel value="configuration"><div className="grid gap-4">
        {operation?.map_body_operation ? <>
          <MapBodyEditor context={context} nodeKey={nodeKey} node={node} onAdd={() => onAddMapBody(nodeKey)} />
          <MapItemsEditor context={context} nodeKey={nodeKey} node={node} />
        </> : null}
        {fields.step.config.filter((field) => !operation?.map_body_operation || !["config.target_step", "config.items"].includes(field.name)).map((field) => <BoundDescriptorField key={field.name} form={context.form} resource={STEP_MODEL} scope={scope} field={{ ...field, readOnly: invalidConfig || field.readOnly }} />)}
        {invalidConfig || !implConfig.hasSchema(node.step_class) ? <BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{ ...fields.step.rawConfig, widget: "json" }} /> : null}
        <CollapsibleRoot variant="section" open={advancedOpen} onOpenChange={setAdvancedOpen}><CollapsibleTrigger><CollapsibleIcon />{t("canvas.advanced")}</CollapsibleTrigger><CollapsiblePanel><div className="grid gap-4 pt-2"><BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={fields.step.key} /><BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{ ...fields.step.joinRule, widget: "select", options: joinRuleOptions }} /></div></CollapsiblePanel></CollapsibleRoot>
      </div></Tabs.Panel>
      <Tabs.Panel value="input"><BoundFormValue form={context.form} name={bindingName}>{(binding) => <WorkflowInputBindingEditor
        nodeKey={nodeKey} operation={operation} value={(binding.value ?? null) as never} readOnly={context.form.formReadOnly}
        messages={[...inputDiagnostics.map((item) => item.message), ...binding.messages]}
        detailPath={pendingIssue?.field === "input_binding" && Array.isArray(pendingIssue.detail_path) ? pendingIssue.detail_path as (string | number)[] : undefined}
        controlRef={binding.controlRef}
        onFocused={onIssueFocused}
        sourceLabel={(stepKey) => Object.values(nodes).find((candidate) => candidate.key === stepKey)?.name}
        onGoToSource={(stepKey) => {
          const source = Object.entries(nodes).find(([, candidate]) => candidate.key === stepKey);
          if (source) onSelectStep(source[0]);
        }}
        onChange={binding.onChange}
        onCommit={binding.onCommit}
        onStructuralChange={(next) => perform(() => context.form.form.setValue(bindingName, next as never, { shouldDirty: true, shouldTouch: true }))}
      />}</BoundFormValue></Tabs.Panel>
    </Tabs>
    {!context.form.formReadOnly ? <div className="flex flex-wrap gap-2">{testStep ? <Button type="button" size="sm" variant="secondary" onClick={() => testStep(nodeKey)}>{t("test.stepSubmit")}</Button> : null}<Button type="button" size="sm" onClick={() => onAddAfter(nodeKey)}>{t("canvas.addAfter")}</Button><Button type="button" size="sm" variant="secondary" onClick={() => onDuplicate(nodeKey)}>{t("canvas.duplicate")}</Button>{!node.is_entry ? <Button type="button" size="sm" variant="secondary" onClick={() => onMakeEntry(nodeKey)}>{t("canvas.makeEntry")}</Button> : null}<Button type="button" size="sm" variant="danger" onClick={() => onDelete(nodeKey)}>{t("canvas.deleteStep")}</Button></div> : null}
  </div>;
}

function MapBodyEditor({ context, nodeKey, node, onAdd }: {
  context: RecordPanelContext;
  nodeKey: string;
  node: DefinitionNode;
  onAdd: () => void;
}): React.ReactElement {
  const t = useWorkflowsT();
  const preview = useWorkflowInputPreview();
  const history = useDefinitionHistoryContext();
  const request = React.useMemo(() => preview?.prepare(nodeKey) ?? null, [node, nodeKey, preview]);
  const query = useAuthoredQuery(
    WorkflowMapBodyCandidatesDocument,
    request ? { ...request, owner: request.target } : undefined,
    { enabled: request != null },
  );
  const response = query.data?.workflow_map_body_candidates;
  React.useEffect(() => {
    if (response?.status === "STALE") preview?.stale();
  }, [preview, response?.status]);
  const candidates = response?.status === "SUCCESS" ? response.candidates : [];
  const config = jsonObject(node.config);
  const currentKey = typeof config.target_step === "string" ? config.target_step : "";
  const options = [
    { value: "", label: t("map.chooseBody") },
    ...candidates.map((candidate) => ({
      value: candidate.step_key,
      label: candidate.eligible ? candidate.label : t("map.unavailableBody", { label: `${candidate.label} — ${candidate.reason ?? ""}` }),
      disabled: !candidate.eligible,
    })),
  ];
  if (currentKey && !options.some((option) => option.value === currentKey)) {
    options.push({ value: currentKey, label: t("map.unavailableBody", { label: currentKey }), disabled: false });
  }
  const changeBody = (targetStep: string): void => {
    const action = () => context.form.form.setValue(
      `definition.nodes.${nodeKey}.config`,
      { ...config, target_step: targetStep } as never,
      { shouldDirty: true, shouldTouch: true },
    );
    history ? history.perform(action) : action();
  };
  return <div className="grid gap-2">
    <BoundFormValue form={context.form} name={`definition.nodes.${nodeKey}.config.target_step`}>{(binding) => <FieldDescriptorControl
      field={{ name: "target_step", label: t("map.body"), description: t("map.bodyDescription"), widget: "select", options }}
      value={currentKey}
      readOnly={context.form.formReadOnly || query.isFetching}
      messages={binding.messages}
      controlRef={binding.controlRef}
      onChange={(value) => changeBody(String(value ?? ""))}
      onCommit={binding.onCommit}
    />}</BoundFormValue>
    {query.error ? <ErrorBanner description={t("map.candidatesError")} /> : null}
    {response?.status === "STRUCTURAL" ? <ErrorBanner description={response.diagnostics.map((item) => item.message).join(" ")} /> : null}
    {!context.form.formReadOnly ? <Button type="button" size="sm" variant="secondary" onClick={onAdd}>{t("map.addBody")}</Button> : null}
  </div>;
}

function MapItemsEditor({ context, nodeKey, node }: {
  context: RecordPanelContext;
  nodeKey: string;
  node: DefinitionNode;
}): React.ReactElement {
  const t = useWorkflowsT();
  const history = useDefinitionHistoryContext();
  const items = jsonObject(node.config).items;
  const mode = Array.isArray(items) ? "literal" : items === "input" ? "input" : "expression";
  const name = `definition.nodes.${nodeKey}.config.items`;
  const setItems = (value: unknown): void => {
    const action = () => context.form.form.setValue(name, value as never, { shouldDirty: true, shouldTouch: true });
    history ? history.perform(action) : action();
  };
  return <div className="grid gap-2">
    <span className="text-sm font-medium">{t("map.items")}</span>
    <div className="flex flex-wrap gap-2">
      <Button type="button" size="sm" variant={mode === "input" ? "primary" : "secondary"} disabled={context.form.formReadOnly} onClick={() => setItems("input")}>{t("map.itemsInput")}</Button>
      <Button type="button" size="sm" variant={mode === "literal" ? "primary" : "secondary"} disabled={context.form.formReadOnly} onClick={() => setItems([])}>{t("map.itemsLiteral")}</Button>
    </div>
    <BoundFormValue form={context.form} name={name}>{(binding) => <FieldDescriptorControl
      field={{
        name: "items",
        label: mode === "literal" ? t("map.itemsLiteralValue") : mode === "input" ? t("map.itemsInputPath") : t("map.itemsLegacy"),
        description: mode === "expression" ? t("map.itemsLegacyDescription") : undefined,
        widget: mode === "literal" ? "json" : "text",
      }}
      value={binding.value}
      messages={binding.messages}
      readOnly={context.form.formReadOnly}
      controlRef={binding.controlRef}
      onChange={binding.onChange}
      onCommit={binding.onCommit}
    />}</BoundFormValue>
  </div>;
}

function EdgeConfigPanel({ context, edgeKey, edge, nodes, operations, diagnostics, pendingIssue, fields, onIssueFocused, onInsert, onDelete }: { context: RecordPanelContext; edgeKey: string; edge: DefinitionEdge; nodes: Record<string, DefinitionNode>; operations: readonly WorkflowOperationChoice[]; diagnostics: WorkflowDefinitionValues["definition"]["readiness"]; pendingIssue: DefinitionDiagnostic | null; fields: WorkflowCanvasFields; onIssueFocused: () => void; onInsert: (id: string) => void; onDelete: (id: string) => void; }): React.ReactElement {
  const t = useWorkflowsT();
  const scope = `definition.edges.${edgeKey}`;
  const options = Object.entries(nodes).map(([value, node]) => ({ value, label: node.name || node.key }));
  const conditionField = outcomeField(fields.edge.condition, edge, nodes, operations, t);
  const edgeErrors = diagnosticsForEdge(diagnostics, edgeKey, edge).map((item) => item.message).join(" ");
  React.useEffect(() => {
    if (!pendingIssue || diagnosticIdentity(pendingIssue, { [edgeKey]: edge }) !== edgeKey || !["source", "target", "condition"].includes(pendingIssue.field)) return;
    context.focusField(`${scope}.${pendingIssue.field}`);
    onIssueFocused();
  }, [context, edge, edgeKey, onIssueFocused, pendingIssue, scope]);
  return <div className="grid min-h-0 gap-4 overflow-auto bg-sheet-1 p-4">
    <ErrorBanner description={edgeErrors || null} />
    <BoundDescriptorField form={context.form} resource={EDGE_MODEL} scope={scope} field={{ ...fields.edge.source, widget: "select", options }} />
    <BoundDescriptorField form={context.form} resource={EDGE_MODEL} scope={scope} field={{ ...fields.edge.target, widget: "select", options }} />
    <BoundDescriptorField form={context.form} resource={EDGE_MODEL} scope={scope} field={conditionField} />
    {!context.form.formReadOnly ? <div className="flex gap-2"><Button type="button" size="sm" onClick={() => onInsert(edgeKey)}>{t("canvas.insertStep")}</Button><Button type="button" size="sm" variant="danger" onClick={() => onDelete(edgeKey)}>{t("canvas.deleteConnection")}</Button></div> : null}
  </div>;
}

type DefinitionDiagnostic = WorkflowDefinitionValues["definition"]["readiness"][number];
type WorkflowCanvasEdgeMeta = { edge: DefinitionEdge | null; mapOwner: string | null };
interface WorkflowCanvasFields {
  step: {
    name: FieldDescriptor;
    operation: FieldDescriptor;
    config: readonly FieldDescriptor[];
    rawConfig: FieldDescriptor;
    inputBinding: FieldDescriptor;
    key: FieldDescriptor;
    joinRule: FieldDescriptor;
  };
  edge: { source: FieldDescriptor; target: FieldDescriptor; condition: FieldDescriptor; };
  workflow: { name: FieldDescriptor; };
  implConfig: ReturnType<typeof useImplConfigFields>;
}
function useWorkflowCanvasFields(t: WorkflowT, operations: NonNullable<Parameters<typeof useImplConfigFields>[2]>): WorkflowCanvasFields {
  const stepMetadata = useModelMetadata(STEP_MODEL);
  const edgeMetadata = useModelMetadata(EDGE_MODEL);
  const workflowMetadata = useModelMetadata("workflows.Workflow");
  const implConfig = useImplConfigFields(STEP_MODEL, "step_class", operations);
  return React.useMemo(() => {
    const stepFields = fieldsWithMetadataDefaults([
      { name: "name" },
      { name: "step_class", label: t("canvas.operation") },
      { name: "config", label: t("canvas.rawConfiguration") },
      { name: "input_binding", label: t("input.tab") },
      { name: "key" },
      { name: "join_rule" },
    ], stepMetadata);
    const edgeFields = fieldsWithMetadataDefaults([
      { name: "source", label: t("canvas.source") },
      { name: "target", label: t("canvas.target") },
      { name: "condition", label: t("canvas.condition") },
    ], edgeMetadata);
    return {
      step: { name: stepFields[0]!, operation: stepFields[1]!, config: fieldsWithMetadataDefaults(implConfig.fields, stepMetadata), rawConfig: stepFields[2]!, inputBinding: stepFields[3]!, key: stepFields[4]!, joinRule: stepFields[5]! },
      edge: { source: edgeFields[0]!, target: edgeFields[1]!, condition: edgeFields[2]! },
      workflow: { name: fieldsWithMetadataDefaults([{ name: "name" }], workflowMetadata)[0]! },
      implConfig,
    };
  }, [edgeMetadata, implConfig, stepMetadata, t, workflowMetadata]);
}
function diagnosticLabel(diagnostic: DefinitionDiagnostic, nodes: Record<string, DefinitionNode>, edges: Record<string, DefinitionEdge>, fields: WorkflowCanvasFields, t: WorkflowT): string {
  const kind = String(diagnostic.kind).toUpperCase();
  if (kind === "NODE") {
    const identity = diagnosticIdentity(diagnostic, nodes);
    const node = identity ? nodes[identity] : undefined;
    const target = node
      ? node.name || node.key || identity!
      : t("canvas.unavailableStep", { identity: diagnosticTargetIdentity(diagnostic) });
    return t("canvas.issueContext", { target, field: nodeFieldLabel(diagnostic.field, node, fields), message: diagnostic.message });
  }
  if (kind === "EDGE") {
    const identity = diagnosticIdentity(diagnostic, edges);
    const edge = identity ? edges[identity] : undefined;
    const target = edge
      ? `${nodeDisplayName(edge.source, nodes)} → ${nodeDisplayName(edge.target, nodes)}`
      : t("canvas.unavailableConnection", { identity: diagnosticTargetIdentity(diagnostic) });
    return t("canvas.issueContext", { target, field: edgeFieldLabel(diagnostic.field, fields), message: diagnostic.message });
  }
  return t("canvas.issueContext", { target: t("canvas.settings"), field: workflowFieldLabel(diagnostic.field, fields), message: diagnostic.message });
}
function nodeFieldLabel(path: string, node: DefinitionNode | undefined, fields: WorkflowCanvasFields): string {
  const descriptor = path === "name" ? fields.step.name
    : path === "step_class" ? fields.step.operation
      : path === "key" ? fields.step.key
        : path === "join_rule" ? fields.step.joinRule
          : path === "input_binding" ? fields.step.inputBinding
          : fields.step.config.find((field) => path === field.name || path.startsWith(`${field.name}.`)) ?? fields.step.rawConfig;
  const resolved = node && descriptor.resolve?.(node as never);
  return descriptorLabel(resolved ? { ...descriptor, ...resolved } : descriptor);
}
function edgeFieldLabel(path: string, fields: WorkflowCanvasFields): string {
  if (path === "source") return descriptorLabel(fields.edge.source);
  if (path === "target") return descriptorLabel(fields.edge.target);
  if (path === "condition") return descriptorLabel(fields.edge.condition);
  return path;
}
function workflowFieldLabel(path: string, fields: WorkflowCanvasFields): string {
  return path === "name" ? descriptorLabel(fields.workflow.name) : path;
}
function descriptorLabel(field: FieldDescriptor): string {
  return typeof field.label === "string" ? field.label : field.name;
}
function diagnosticTargetIdentity(diagnostic: DefinitionDiagnostic): string {
  return String(diagnostic.client_key ?? diagnostic.id ?? diagnostic.requested_id ?? "unknown");
}
function nodeDisplayName(identity: string, nodes: Record<string, DefinitionNode>): string {
  const node = nodes[identity];
  return node?.name || node?.key || identity;
}
function operationChoice(operations: readonly WorkflowOperationChoice[], value: unknown): WorkflowOperationChoice | undefined {
  const exact = operations.find((operation) => operation.key === value);
  if (exact) return exact;
  const options = operations.map((operation) => ({ value: operation.key, label: operation.label }));
  const key = canonicalOptionValue(options, value);
  return operations.find((operation) => operation.key === key);
}
function outcomeChoiceForEdge(
  edge: Pick<DefinitionEdge, "condition" | "source">,
  nodes: Record<string, DefinitionNode>,
  operations: readonly WorkflowOperationChoice[],
) {
  const operation = operationChoice(operations, nodes[edge.source]?.step_class);
  return operation?.outcomes?.find((outcome) => outcome.key === edge.condition);
}
function outcomeField(
  field: FieldDescriptor,
  edge: Pick<DefinitionEdge, "condition" | "source">,
  nodes: Record<string, DefinitionNode>,
  operations: readonly WorkflowOperationChoice[],
  t: WorkflowT,
): FieldDescriptor {
  const operation = operationChoice(operations, nodes[edge.source]?.step_class);
  const declared = operation?.outcomes ?? [];
  if (!declared.length) return field;
  const options = [
    { value: "", label: t("canvas.anyOutcome") },
    ...declared.map((outcome) => ({ value: outcome.key, label: outcome.label })),
  ];
  if (edge.condition && !declared.some((outcome) => outcome.key === edge.condition)) {
    options.push({ value: edge.condition, label: t("canvas.unavailableOutcome", { outcome: edge.condition }) });
  }
  const selected = declared.find((outcome) => outcome.key === edge.condition);
  return { ...field, widget: "select", options, description: selected?.description || field.description };
}
function diagnosticsForNode(diagnostics: readonly DefinitionDiagnostic[], identity: string, node: DefinitionNode): DefinitionDiagnostic[] {
  return diagnostics.filter((item) => String(item.kind).toUpperCase() === "NODE" && ((item.id != null && item.id === node.id) || (item.client_key != null && item.client_key === identity)));
}
function diagnosticsForEdge(diagnostics: readonly DefinitionDiagnostic[], identity: string, edge: DefinitionEdge): DefinitionDiagnostic[] {
  return diagnostics.filter((item) => String(item.kind).toUpperCase() === "EDGE" && ((item.id != null && item.id === edge.id) || (item.client_key != null && item.client_key === identity)));
}
function issueLabel(count: number, t: WorkflowT): string { return t(count === 1 ? "canvas.oneIssue" : "canvas.manyIssues", { count }); }
function edgeAriaLabel(edge: DefinitionEdge, nodes: Record<string, DefinitionNode>, outcomeLabel: string, count: number, t: WorkflowT): string {
  const source = nodes[edge.source]?.name || nodes[edge.source]?.key || edge.source;
  const target = nodes[edge.target]?.name || nodes[edge.target]?.key || edge.target;
  return [t("canvas.connectionAria", { source, target }), outcomeLabel ? t("canvas.outcomeAria", { outcome: outcomeLabel }) : null, count ? issueLabel(count, t) : null].filter(Boolean).join(", ");
}
function diagnosticIdentity(diagnostic: DefinitionDiagnostic, rows: Record<string, { id: string; clientKey?: string; }>): string | null {
  return Object.entries(rows).find(([key, row]) => (
    (diagnostic.client_key != null && (key === diagnostic.client_key || row.clientKey === diagnostic.client_key))
    || (diagnostic.id != null && (key === diagnostic.id || row.id === diagnostic.id))
  ))?.[0] ?? null;
}
function nodeDiagnosticField(field: string, configFields: readonly string[], rawConfig: boolean): string | null {
  if (["name", "step_class", "key", "join_rule"].includes(field)) return field;
  const declared = [...configFields].sort((left, right) => right.length - left.length).find((name) => field === name || field.startsWith(`${name}.`));
  if (declared) return declared;
  return rawConfig && (field === "config" || field.startsWith("config.")) ? "config" : null;
}

function operationOptions(operations: readonly { key: string; label: string; selectable: boolean; }[], current?: unknown): { value: string; label: string; disabled: boolean; }[] {
  const options = operations.map((operation) => ({ value: operation.key, label: operation.label, disabled: !operation.selectable }));
  const currentKey = canonicalOptionValue(options, current);
  return options.filter((option) => !option.disabled || option.value === currentKey);
}
type WorkflowT = ReturnType<typeof useWorkflowsT>;
function operationHint(operation: { description: string; effect: string; effect_description: string; } | undefined, t: WorkflowT): string {
  if (!operation) return t("canvas.operationUnavailable");
  return [operation.description, t("canvas.effectHint", { effect: operationEffectLabel(operation.effect, t) }), operation.effect_description].filter(Boolean).join(" ");
}
function operationEffectLabel(effect: string, t: WorkflowT): string {
  switch (effect) { case "NONE": return t("canvas.effect.none"); case "READ": return t("canvas.effect.read"); case "WRITE": return t("canvas.effect.write"); case "EXTERNAL": return t("canvas.effect.external"); default: return t("canvas.effect.unknown"); }
}

function jsonObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? structuredClone(value) as Record<string, unknown> : {};
}
function uniqueNodeKey(label: string, nodes: Record<string, DefinitionNode>): string {
  const base = slugify(label).slice(0, 100) || "step";
  const used = new Set(Object.values(nodes).map((node) => node.key));
  if (!used.has(base)) return base;
  let suffix = 2;
  let candidate = `${base.slice(0, 100 - String(suffix).length - 1)}-${suffix}`;
  while (used.has(candidate)) {
    suffix += 1;
    candidate = `${base.slice(0, 100 - String(suffix).length - 1)}-${suffix}`;
  }
  return candidate;
}
function nodeOptions(nodes: Record<string, DefinitionNode>): { value: string; label: string; }[] {
  return Object.entries(nodes).map(([value, node]) => ({ value, label: node.name || node.key }));
}

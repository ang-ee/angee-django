import * as React from "react";
import { useAuthoredQuery } from "@angee/refine";
import {
  Badge,
  BoundDescriptorField,
  Button,
  CollapsibleIcon,
  CollapsiblePanel,
  CollapsibleRoot,
  CollapsibleTrigger,
  EmptyState,
  ErrorBanner,
  GraphView,
  Glyph,
  MutationDialog,
  SplitPane,
  SplitPaneHandle,
  SplitPanes,
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
  type GraphViewNode,
  type GraphViewPosition,
  type RecordPanelContext,
} from "@angee/ui";

import { WorkflowStepOperationsDocument } from "../documents.console";
import { useWorkflowsT } from "../i18n";
import { WorkflowOperationPicker, type WorkflowOperationChoice } from "./WorkflowOperationPicker";
import { workflowNodeStyles, type WorkflowGraphNodeKind } from "./graph-data";
import { EMPTY_WORKFLOW_EDITOR_SELECTION, workflowEditorSelection } from "./workflow-editor-state";
import type { DefinitionEdge, DefinitionNode, WorkflowDefinitionValues } from "./workflow-definition-state";
import { useDefinitionHistoryContext } from "./workflow-definition-history";

const STEP_MODEL = "workflows.Step";
const EDGE_MODEL = "workflows.Edge";

export function WorkflowCanvas({ context }: { context: RecordPanelContext }): React.ReactElement {
  const t = useWorkflowsT();
  const values = useFormViewValues(context.form) as WorkflowDefinitionValues;
  const history = useDefinitionHistoryContext();
  const perform = React.useCallback((action: () => void) => history ? history.perform(action) : action(), [history]);
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
  const operationsQuery = useAuthoredQuery(WorkflowStepOperationsDocument);
  const operations = operationsQuery.data?.workflow_step_operations ?? [];
  const diagnostics = values.definition?.readiness ?? [];
  const [pendingIssue, setPendingIssue] = React.useState<DefinitionDiagnostic | null>(null);
  const [issuesOpen, setIssuesOpen] = React.useState(false);
  const [palette, setPalette] = React.useState<{ kind: "first" | "after" | "insert"; identity?: string } | null>(null);
  const [connectOpen, setConnectOpen] = React.useState(false);
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
  const graphNodes = React.useMemo(() => Object.entries(nodes).map(([identity, node]) => {
    const operation = operationChoice(operations, node.step_class);
    const title = node.name || operation?.label || node.key;
    const issueCount = diagnosticsForNode(diagnostics, identity, node).length;
    const secondary = node.name && operation?.label && node.name !== operation.label ? operation.label : null;
    return {
      id: identity,
      kind: nodeKind(node.step_class),
      kindLabel: operation?.category || t("canvas.operationUnavailableShort"),
      ariaLabel: [title, secondary, operation?.category, node.is_entry ? t("canvas.start") : null, issueCount ? issueLabel(issueCount, t) : null].filter(Boolean).join(", "),
      title,
      detail: [secondary, node.is_entry ? t("canvas.start") : null, issueCount ? issueLabel(issueCount, t) : null].filter(Boolean).join(" · "),
      highlighted: node.is_entry,
      position: graphPosition(node.position),
      meta: { node },
    };
  }) satisfies GraphViewNode<WorkflowGraphNodeKind, { node: DefinitionNode }>[], [diagnostics, nodes, operations, t]);
  const graphEdges = React.useMemo(() => Object.entries(edges).map(([identity, edge]) => ({
    id: identity,
    source: edge.source,
    target: edge.target,
    kind: edge.condition ? "condition" as const : "default" as const,
    label: edge.condition || undefined,
    ariaLabel: edgeAriaLabel(edge, nodes, diagnosticsForEdge(diagnostics, identity, edge).length, t),
    meta: { edge },
  })) satisfies GraphViewEdge<"condition" | "default", { edge: DefinitionEdge }>[], [diagnostics, edges, nodes, t]);
  const currentGraph = React.useCallback(() => ({
    nodes: (context.form.form.getValues("definition.nodes") ?? {}) as Record<string, DefinitionNode>,
    edges: (context.form.form.getValues("definition.edges") ?? {}) as Record<string, DefinitionEdge>,
  }), [context.form.form]);

  const handleNodeDragEnd = React.useCallback((node: GraphViewNode<WorkflowGraphNodeKind, { node: DefinitionNode }>, position: GraphViewPosition) => {
    perform(() => context.form.form.setValue(`definition.nodes.${node.id}.position`, position as never, {
      shouldDirty: true, shouldTouch: true,
    }));
  }, [context.form.form, perform]);
  const handleConnect = React.useCallback((edge: GraphViewConnection & { condition?: string }) => {
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
    if (palette.identity && palette.kind === "after" && !latest.nodes[palette.identity]) return;
    if (palette.identity && palette.kind === "insert" && !latest.edges[palette.identity]) return;
    const clientKey = `node-${crypto.randomUUID()}`;
    const defaults = jsonObject(operation.defaults);
    const defaultName = typeof defaults.name === "string" ? defaults.name : operation.label;
    const defaultKey = typeof defaults.key === "string" ? defaults.key : defaultName;
    const node: DefinitionNode = {
      id: "", clientKey, key: uniqueNodeKey(defaultKey || operation.key, latest.nodes), name: defaultName,
      step_class: operation.key, config: jsonObject(defaults.config), config_errors: {}, join_rule: typeof defaults.join_rule === "string" ? defaults.join_rule : "ALL_SUCCESS",
      is_entry: Object.keys(latest.nodes).length === 0, position: insertionPosition(palette, latest.nodes, latest.edges),
    };
    const updated = graphWithOperation(node, palette, latest.nodes, latest.edges);
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
    const copy = { ...structuredClone(source), id: "", clientKey, key: uniqueNodeKey(`${source.key}-copy`, latest.nodes), name: `${source.name} copy`, config_errors: {}, is_entry: false, position: offsetPosition(source.position) };
    perform(() => context.form.form.setValue("definition.nodes", { ...latest.nodes, [clientKey]: copy } as never, { shouldDirty: true, shouldTouch: true }));
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
        <CollapsiblePanel><div className="grid max-h-40 gap-1 overflow-auto pb-2" aria-label={t("canvas.issues")}><span className="px-2 text-12 text-fg-muted">{t("canvas.unsavedIssuesHint")}</span>{diagnostics.map((diagnostic, index) => <Button key={`${diagnostic.code}-${diagnostic.kind}-${diagnostic.id ?? diagnostic.client_key ?? "unknown"}-${diagnostic.field}-${index}`} type="button" size="sm" variant="ghost" className="h-auto justify-start whitespace-normal text-left" onClick={() => {
          const kind = String(diagnostic.kind).toUpperCase();
          if (kind === "NODE") {
            const identity = diagnosticIdentity(diagnostic, nodes); if (!identity) return;
            setPendingIssue(diagnostic); dispatchSelection({ type: "select-step", id: identity });
          } else if (kind === "EDGE") {
            const identity = diagnosticIdentity(diagnostic, edges); if (!identity) return;
            setPendingIssue(diagnostic); dispatchSelection({ type: "select-edge", id: identity });
          } else {
            context.focusField(diagnostic.field, { recordTabId: "overview" });
          }
          if (!wide) setIssuesOpen(false);
        }}>{diagnostic.message}</Button>)}</div></CollapsiblePanel>
      </CollapsibleRoot> : null}
    <SplitPanes ref={containerRef} autoSave="workflows.canvas" persistLayout={wide} panelIds={["graph", "inspector"]} className="min-h-0 flex-1 bg-canvas">
      <SplitPane id="graph" defaultSize={68} minSize={42} collapsible panelRef={graphPane.panelRef} onResize={graphPane.onResize} className={!wide && showingInspector ? "hidden" : "relative"}>
        {graphNodes.length === 0 ? (
          <EmptyState fill icon="workflow-canvas" title={t("canvas.emptyTitle")} description={t("canvas.emptyDescription")} actions={!readOnly ? <Button type="button" onClick={() => setPalette({ kind: "first" })}>{t("canvas.addFirst")}</Button> : null} />
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
        {!readOnly && graphNodes.length ? <div className="absolute right-3 top-3 flex gap-2"><Button type="button" size="sm" variant="secondary" onClick={() => setConnectOpen(true)}>{t("canvas.connectSteps")}</Button><Button type="button" size="sm" onClick={() => setPalette({ kind: "first" })}>{t("canvas.addStep")}</Button></div> : null}
      </SplitPane>
      <SplitPaneHandle className={!wide ? "hidden" : undefined} />
      <SplitPane id="inspector" defaultSize={32} minSize={22} collapsible panelRef={inspectorPane.panelRef} onResize={inspectorPane.onResize} className={!wide && !showingInspector ? "hidden" : undefined}>
        {!wide && hasSelection ? <Button type="button" variant="ghost" size="sm" onClick={() => dispatchSelection({ type: "show-canvas" })}><Glyph name="chevron-left" />{t("canvas.back")}</Button> : null}
        <CanvasInspector context={context} nodeKey={selectedStep} edgeKey={selectedEdge} node={selectedStep ? nodes[selectedStep] : undefined} edge={selectedEdge ? edges[selectedEdge] : undefined} nodes={nodes} diagnostics={diagnostics} pendingIssue={pendingIssue} onIssueFocused={() => setPendingIssue(null)} onAddAfter={(identity) => setPalette({ kind: "after", identity })} onInsert={(identity) => setPalette({ kind: "insert", identity })} onDuplicate={duplicateNode} onDelete={deleteNode} onDeleteEdge={deleteEdge} onMakeEntry={makeEntry} />
      </SplitPane>
      <WorkflowOperationPicker operations={operations as readonly WorkflowOperationChoice[]} open={palette !== null} onOpenChange={(open) => { if (!open) setPalette(null); }} onChoose={chooseOperation} loading={operationsQuery.isFetching} error={operationsQuery.error ? errorMessage(operationsQuery.error) : null} />
      <MutationDialog
        open={connectOpen}
        onOpenChange={setConnectOpen}
        title={t("canvas.connectSteps")}
        description={t("canvas.connectDescription")}
        fields={[
          { name: "source", label: t("canvas.source"), widget: "select", options: nodeOptions(nodes), required: true },
          { name: "target", label: t("canvas.target"), widget: "select", options: nodeOptions(nodes), required: true },
          { name: "condition", label: t("canvas.condition") },
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

function CanvasInspector({ context, nodeKey, edgeKey, node, edge, nodes, diagnostics, pendingIssue, onIssueFocused, onAddAfter, onInsert, onDuplicate, onDelete, onDeleteEdge, onMakeEntry }: { context: RecordPanelContext; nodeKey: string | null; edgeKey: string | null; node?: DefinitionNode; edge?: DefinitionEdge; nodes: Record<string, DefinitionNode>; diagnostics: WorkflowDefinitionValues["definition"]["readiness"]; pendingIssue: DefinitionDiagnostic | null; onIssueFocused: () => void; onAddAfter: (id: string) => void; onInsert: (id: string) => void; onDuplicate: (id: string) => void; onDelete: (id: string) => void; onDeleteEdge: (id: string) => void; onMakeEntry: (id: string) => void }): React.ReactElement {
  const t = useWorkflowsT();
  if (nodeKey && node) return <StepConfigPanel context={context} nodeKey={nodeKey} node={node} diagnostics={diagnostics} pendingIssue={pendingIssue} onIssueFocused={onIssueFocused} onAddAfter={onAddAfter} onDuplicate={onDuplicate} onDelete={onDelete} onMakeEntry={onMakeEntry} />;
  if (edgeKey && edge) return <EdgeConfigPanel context={context} edgeKey={edgeKey} edge={edge} nodes={nodes} diagnostics={diagnostics} pendingIssue={pendingIssue} onIssueFocused={onIssueFocused} onInsert={onInsert} onDelete={onDeleteEdge} />;
  return <div className="min-h-0 overflow-auto bg-sheet-1 p-4"><EmptyState icon="workflow-step" title={t("canvas.stepConfig")} description={t("canvas.selectStep")} /></div>;
}

function StepConfigPanel({ context, nodeKey, node, diagnostics, pendingIssue, onIssueFocused, onAddAfter, onDuplicate, onDelete, onMakeEntry }: { context: RecordPanelContext; nodeKey: string; node: DefinitionNode; diagnostics: WorkflowDefinitionValues["definition"]["readiness"]; pendingIssue: DefinitionDiagnostic | null; onIssueFocused: () => void; onAddAfter: (id: string) => void; onDuplicate: (id: string) => void; onDelete: (id: string) => void; onMakeEntry: (id: string) => void }): React.ReactElement {
  const t = useWorkflowsT();
  const operationsQuery = useAuthoredQuery(WorkflowStepOperationsDocument);
  const operations = operationsQuery.data?.workflow_step_operations ?? [];
  const options = React.useMemo(() => operationOptions(operations, node.step_class), [node.step_class, operations]);
  const prefill = useImplPrefill(STEP_MODEL, "step_class", {}, operations);
  const implConfig = useImplConfigFields(STEP_MODEL, "step_class", operations);
  const joinRuleOptions = useEnumOptions(STEP_MODEL, "join_rule");
  const scope = `definition.nodes.${nodeKey}`;
  const invalidConfig = node.config == null || typeof node.config !== "object" || Array.isArray(node.config);
  const [advancedOpen, setAdvancedOpen] = React.useState(false);
  const issueTarget = pendingIssue && diagnosticIdentity(pendingIssue, { [nodeKey]: node }) === nodeKey
    ? nodeDiagnosticField(pendingIssue.field, implConfig.fields.map((field) => field.name), invalidConfig || !implConfig.hasSchema(node.step_class))
    : null;
  React.useEffect(() => {
    if (issueTarget === "key" || issueTarget === "join_rule") setAdvancedOpen(true);
  }, [issueTarget]);
  React.useEffect(() => {
    if (!issueTarget || ((issueTarget === "key" || issueTarget === "join_rule") && !advancedOpen)) return;
    context.focusField(`${scope}.${issueTarget}`);
    onIssueFocused();
  }, [advancedOpen, context, issueTarget, onIssueFocused, scope]);
  const nodeErrors = [configErrorMessage(node.config_errors), ...diagnosticsForNode(diagnostics, nodeKey, node).map((item) => item.message)].filter(Boolean).join(" ");
  return <div className="grid min-h-0 gap-4 overflow-auto bg-sheet-1 p-4">
    <ErrorBanner description={operationsQuery.error ? errorMessage(operationsQuery.error) : null} />
    <ErrorBanner description={nodeErrors || null} />
    <BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{ name: "name" }} />
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
    <CollapsibleRoot variant="section" open={advancedOpen} onOpenChange={setAdvancedOpen}><CollapsibleTrigger><CollapsibleIcon />{t("canvas.advanced")}</CollapsibleTrigger><CollapsiblePanel><div className="grid gap-4 pt-2"><BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{ name: "key" }} /><BoundDescriptorField form={context.form} resource={STEP_MODEL} scope={scope} field={{ name: "join_rule", widget: "select", options: joinRuleOptions }} /></div></CollapsiblePanel></CollapsibleRoot>
    {!context.form.formReadOnly ? <div className="flex flex-wrap gap-2"><Button type="button" size="sm" onClick={() => onAddAfter(nodeKey)}>{t("canvas.addAfter")}</Button><Button type="button" size="sm" variant="secondary" onClick={() => onDuplicate(nodeKey)}>{t("canvas.duplicate")}</Button>{!node.is_entry ? <Button type="button" size="sm" variant="secondary" onClick={() => onMakeEntry(nodeKey)}>{t("canvas.makeEntry")}</Button> : null}<Button type="button" size="sm" variant="danger" onClick={() => onDelete(nodeKey)}>{t("canvas.deleteStep")}</Button></div> : null}
  </div>;
}

function EdgeConfigPanel({ context, edgeKey, edge, nodes, diagnostics, pendingIssue, onIssueFocused, onInsert, onDelete }: { context: RecordPanelContext; edgeKey: string; edge: DefinitionEdge; nodes: Record<string, DefinitionNode>; diagnostics: WorkflowDefinitionValues["definition"]["readiness"]; pendingIssue: DefinitionDiagnostic | null; onIssueFocused: () => void; onInsert: (id: string) => void; onDelete: (id: string) => void }): React.ReactElement {
  const t = useWorkflowsT();
  const scope = `definition.edges.${edgeKey}`;
  const options = Object.entries(nodes).map(([value, node]) => ({ value, label: node.name || node.key }));
  const edgeErrors = diagnosticsForEdge(diagnostics, edgeKey, edge).map((item) => item.message).join(" ");
  React.useEffect(() => {
    if (!pendingIssue || diagnosticIdentity(pendingIssue, { [edgeKey]: edge }) !== edgeKey || !["source", "target", "condition"].includes(pendingIssue.field)) return;
    context.focusField(`${scope}.${pendingIssue.field}`);
    onIssueFocused();
  }, [context, edge, edgeKey, onIssueFocused, pendingIssue, scope]);
  return <div className="grid min-h-0 gap-4 overflow-auto bg-sheet-1 p-4">
    <ErrorBanner description={edgeErrors || null} />
    <BoundDescriptorField form={context.form} resource={EDGE_MODEL} scope={scope} field={{ name: "source", label: t("canvas.source"), widget: "select", options }} />
    <BoundDescriptorField form={context.form} resource={EDGE_MODEL} scope={scope} field={{ name: "target", label: t("canvas.target"), widget: "select", options }} />
    <BoundDescriptorField form={context.form} resource={EDGE_MODEL} scope={scope} field={{ name: "condition", label: t("canvas.condition") }} />
    {!context.form.formReadOnly ? <div className="flex gap-2"><Button type="button" size="sm" onClick={() => onInsert(edgeKey)}>{t("canvas.insertStep")}</Button><Button type="button" size="sm" variant="danger" onClick={() => onDelete(edgeKey)}>{t("canvas.deleteConnection")}</Button></div> : null}
  </div>;
}

type DefinitionDiagnostic = WorkflowDefinitionValues["definition"]["readiness"][number];
function operationChoice(operations: readonly WorkflowOperationChoice[], value: unknown): WorkflowOperationChoice | undefined {
  const options = operations.map((operation) => ({ value: operation.key, label: operation.label }));
  const key = canonicalOptionValue(options, value);
  return operations.find((operation) => operation.key === key);
}
function diagnosticsForNode(diagnostics: readonly DefinitionDiagnostic[], identity: string, node: DefinitionNode): DefinitionDiagnostic[] {
  return diagnostics.filter((item) => String(item.kind).toUpperCase() === "NODE" && ((item.id != null && item.id === node.id) || (item.client_key != null && item.client_key === identity)));
}
function diagnosticsForEdge(diagnostics: readonly DefinitionDiagnostic[], identity: string, edge: DefinitionEdge): DefinitionDiagnostic[] {
  return diagnostics.filter((item) => String(item.kind).toUpperCase() === "EDGE" && ((item.id != null && item.id === edge.id) || (item.client_key != null && item.client_key === identity)));
}
function issueLabel(count: number, t: WorkflowT): string { return t(count === 1 ? "canvas.oneIssue" : "canvas.manyIssues", { count }); }
function edgeAriaLabel(edge: DefinitionEdge, nodes: Record<string, DefinitionNode>, count: number, t: WorkflowT): string {
  const source = nodes[edge.source]?.name || nodes[edge.source]?.key || edge.source;
  const target = nodes[edge.target]?.name || nodes[edge.target]?.key || edge.target;
  return [t("canvas.connectionAria", { source, target }), edge.condition ? t("canvas.outcomeAria", { outcome: edge.condition }) : null, count ? issueLabel(count, t) : null].filter(Boolean).join(", ");
}
function diagnosticIdentity(diagnostic: DefinitionDiagnostic, rows: Record<string, { id: string; clientKey?: string }>): string | null {
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
function newEdge(source: string, target: string, condition = ""): Record<string, DefinitionEdge> {
  const clientKey = `edge-${crypto.randomUUID()}`;
  return { [clientKey]: { id: "", clientKey, source, target, condition } as DefinitionEdge };
}
export function graphWithOperation(node: DefinitionNode, palette: { kind: "first" | "after" | "insert"; identity?: string }, nodes: Record<string, DefinitionNode>, edges: Record<string, DefinitionEdge>): { nodes: Record<string, DefinitionNode>; edges: Record<string, DefinitionEdge> } | null {
  const identity = node.clientKey || node.id;
  const nextNodes = { ...nodes, [identity]: node };
  if (palette.kind === "after" && palette.identity) return { nodes: nextNodes, edges: { ...edges, ...newEdge(palette.identity, identity) } };
  if (palette.kind === "insert" && palette.identity) {
    const replaced = edges[palette.identity];
    if (!replaced) return null;
    const { [palette.identity]: _removed, ...remaining } = edges;
    return { nodes: nextNodes, edges: { ...remaining, ...newEdge(replaced.source, identity, replaced.condition), ...newEdge(identity, replaced.target) } };
  }
  return { nodes: nextNodes, edges };
}
function nodeOptions(nodes: Record<string, DefinitionNode>): { value: string; label: string }[] {
  return Object.entries(nodes).map(([value, node]) => ({ value, label: node.name || node.key }));
}
function insertionPosition(palette: { kind: "first" | "after" | "insert"; identity?: string }, nodes: Record<string, DefinitionNode>, edges: Record<string, DefinitionEdge>): GraphViewPosition {
  if (palette.kind === "after" && palette.identity) return offsetPosition(nodes[palette.identity]?.position);
  if (palette.kind === "insert" && palette.identity) {
    const edge = edges[palette.identity]; const source = edge ? graphPosition(nodes[edge.source]?.position) : undefined; const target = edge ? graphPosition(nodes[edge.target]?.position) : undefined;
    if (source && target) return { x: (source.x + target.x) / 2, y: (source.y + target.y) / 2 };
  }
  return { x: 80 + Object.keys(nodes).length * 40, y: 80 + Object.keys(nodes).length * 40 };
}
function offsetPosition(value: unknown): GraphViewPosition {
  const position = graphPosition(value) ?? { x: 80, y: 80 };
  return { x: position.x + 180, y: position.y };
}

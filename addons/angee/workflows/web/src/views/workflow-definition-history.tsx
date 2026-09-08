import * as React from "react";
import type { RecordPanelContext } from "@angee/ui";

import { EDGE_FIELDS, NODE_FIELDS, WORKFLOW_FIELDS, definitionValueEqual, type DefinitionEdge, type DefinitionNode, type WorkflowDefinitionValues } from "./workflow-definition-state";

export interface EditableFrame {
  workflow: Record<string, unknown>;
  nodes: WorkflowDefinitionValues["definition"]["nodes"];
  edges: WorkflowDefinitionValues["definition"]["edges"];
}

export interface DefinitionHistory {
  canUndo: boolean;
  canRedo: boolean;
  perform: (action: () => void) => void;
  start: (path: string) => void;
  commit: (path: string) => void;
  undo: () => void;
  redo: () => void;
  reset: () => void;
  rebase: (baseline: WorkflowDefinitionValues, submitted: WorkflowDefinitionValues, correlations: { nodes: readonly { client_key: string; id: string }[]; edges: readonly { client_key: string; id: string }[] }) => void;
}

const HistoryContext = React.createContext<DefinitionHistory | null>(null);
export const DefinitionHistoryProvider = HistoryContext.Provider;
export function useDefinitionHistoryContext(): DefinitionHistory | null { return React.useContext(HistoryContext); }

export function useDefinitionHistory(surface: React.RefObject<RecordPanelContext["form"] | null>, readOnly: boolean): DefinitionHistory {
  const past = React.useRef<EditableFrame[]>([]);
  const future = React.useRef<EditableFrame[]>([]);
  const active = React.useRef<{ path: string; before: EditableFrame } | null>(null);
  const [, redraw] = React.useReducer((value) => value + 1, 0);
  const capture = React.useCallback(() => surface.current ? editableFrame(surface.current.form.getValues() as WorkflowDefinitionValues) : null, [surface]);
  const record = React.useCallback((before: EditableFrame | null) => {
    const after = capture();
    if (!before || !after || definitionValueEqual(before, after)) return;
    past.current.push(before);
    future.current = [];
    redraw();
  }, [capture]);
  const apply = React.useCallback((frame: EditableFrame) => {
    const owner = surface.current;
    if (!owner) return;
    const current = owner.form.getValues() as WorkflowDefinitionValues;
    owner.form.reset(applyFrame(current, frame), { keepDefaultValues: true });
  }, [surface]);
  const perform = React.useCallback((action: () => void) => {
    if (readOnly) return;
    const before = capture();
    action();
    record(before);
  }, [capture, readOnly, record]);
  const start = React.useCallback((path: string) => {
    if (readOnly || active.current) return;
    const before = capture();
    if (before) active.current = { path, before };
  }, [capture, readOnly]);
  const commit = React.useCallback((path: string) => {
    if (active.current?.path !== path) return;
    const before = active.current.before;
    active.current = null;
    record(before);
  }, [record]);
  const undo = React.useCallback(() => {
    if (readOnly) return;
    const frame = past.current.pop();
    const current = capture();
    if (!frame || !current) return;
    future.current.push(current); apply(frame); redraw();
  }, [apply, capture, readOnly]);
  const redo = React.useCallback(() => {
    if (readOnly) return;
    const frame = future.current.pop();
    const current = capture();
    if (!frame || !current) return;
    past.current.push(current); apply(frame); redraw();
  }, [apply, capture, readOnly]);
  const rebase = React.useCallback((baseline: WorkflowDefinitionValues, submitted: WorkflowDefinitionValues, correlations: { nodes: readonly { client_key: string; id: string }[]; edges: readonly { client_key: string; id: string }[] }) => {
    const transform = historyRebaser(baseline, submitted, correlations);
    past.current = past.current.map(transform);
    future.current = future.current.map(transform);
    if (active.current) active.current = { ...active.current, before: transform(active.current.before) };
    const live = capture();
    if (live && surface.current) {
      const rebased = transform(live);
      const currentValues = surface.current.form.getValues() as WorkflowDefinitionValues;
      surface.current.form.setValue("definition.nodes", appliedNodes(currentValues.definition.nodes, rebased.nodes) as never, { shouldDirty: true });
      surface.current.form.setValue("definition.edges", rebased.edges as never, { shouldDirty: true });
    }
  }, [capture, surface]);
  const reset = React.useCallback(() => {
    past.current = [];
    future.current = [];
    active.current = null;
    redraw();
  }, []);
  return { canUndo: past.current.length > 0, canRedo: future.current.length > 0, perform, start, commit, undo, redo, reset, rebase };
}

function editableFrame(values: WorkflowDefinitionValues): EditableFrame {
  return {
    workflow: Object.fromEntries(WORKFLOW_FIELDS.map((field) => [field, structuredClone(values[field])])),
    nodes: Object.fromEntries(Object.entries(values.definition.nodes).map(([identity, node]) => [identity, pickNode(node)])),
    edges: Object.fromEntries(Object.entries(values.definition.edges).map(([identity, edge]) => [identity, pickEdge(edge)])),
  };
}
function pickNode(node: DefinitionNode): DefinitionNode {
  return Object.fromEntries(["id", "clientKey", ...NODE_FIELDS].flatMap((field) => {
    const value = node[field as keyof DefinitionNode];
    return value === undefined ? [] : [[field, structuredClone(value)]];
  })) as DefinitionNode;
}
function pickEdge(edge: DefinitionEdge): DefinitionEdge {
  return Object.fromEntries(["id", "clientKey", "source", "target", ...EDGE_FIELDS].flatMap((field) => {
    const value = edge[field as keyof DefinitionEdge];
    return value === undefined ? [] : [[field, structuredClone(value)]];
  })) as DefinitionEdge;
}
function applyFrame(current: WorkflowDefinitionValues, frame: EditableFrame): WorkflowDefinitionValues {
  return { ...current, ...structuredClone(frame.workflow), definition: { ...current.definition, nodes: appliedNodes(current.definition.nodes, frame.nodes), edges: structuredClone(frame.edges) } } as WorkflowDefinitionValues;
}
function appliedNodes(current: WorkflowDefinitionValues["definition"]["nodes"], editable: WorkflowDefinitionValues["definition"]["nodes"]): WorkflowDefinitionValues["definition"]["nodes"] {
  return Object.fromEntries(Object.entries(editable).map(([identity, node]) => [identity, {
    ...(current[identity] ?? { config_errors: {} }),
    ...structuredClone(node),
  }]));
}
export function historyRebaser(baseline: WorkflowDefinitionValues, submitted: WorkflowDefinitionValues, correlations: { nodes: readonly { client_key: string; id: string }[]; edges: readonly { client_key: string; id: string }[] }): (frame: EditableFrame) => EditableFrame {
  const nodeIds = new Map(correlations.nodes.map((item) => [item.client_key, item.id]));
  const edgeIds = new Map(correlations.edges.map((item) => [item.client_key, item.id]));
  const deletedNodes = Object.keys(baseline.definition.nodes).filter((key) => !submitted.definition.nodes[key]);
  const deletedEdges = Object.keys(baseline.definition.edges).filter((key) => !submitted.definition.edges[key]);
  const replacementNodes = new Map(deletedNodes.map((key) => [key, `node-${crypto.randomUUID()}`]));
  const replacementEdges = new Map(deletedEdges.map((key) => [key, `edge-${crypto.randomUUID()}`]));
  return (frame) => {
    const nodes = Object.fromEntries(Object.entries(frame.nodes).map(([key, node]) => {
      const replacement = replacementNodes.get(key);
      if (replacement) return [replacement, { ...node, id: "", clientKey: replacement }];
      return [key, nodeIds.has(key) ? { ...node, id: nodeIds.get(key)! } : node];
    }));
    const edges = Object.fromEntries(Object.entries(frame.edges).map(([key, edge]) => {
      const replacement = replacementEdges.get(key);
      const identity = replacement ?? key;
      return [identity, {
        ...edge,
        ...(replacement ? { id: "", clientKey: replacement } : edgeIds.has(key) ? { id: edgeIds.get(key)! } : {}),
        source: replacementNodes.get(edge.source) ?? edge.source,
        target: replacementNodes.get(edge.target) ?? edge.target,
      }];
    }));
    return { workflow: frame.workflow, nodes, edges };
  };
}

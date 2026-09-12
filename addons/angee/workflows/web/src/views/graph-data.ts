import {
  graphNodeStyle,
  type GraphViewEdge,
  type GraphViewNode,
  type GraphViewNodeStyle,
  type GraphViewPosition,
} from "@angee/ui";

import type {
  WorkflowGraphEdge,
  WorkflowGraphStep,
  WorkflowRunStepRun,
} from "../documents.console";

export type WorkflowGraphNodeKind =
  | "AGENT"
  | "GATE"
  | "HANDLER"
  | "MAP"
  | "WAIT"
  | "SCHEDULED"
  | "STARTED"
  | "WAITING"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELED"
  | "SKIPPED";

export type WorkflowGraphEdgeKind = "default" | "condition";

export const workflowNodeStyles = {
  AGENT: graphNodeStyle("var(--brand)", "brand"),
  GATE: graphNodeStyle("var(--warning)", "warning", { background: "var(--warning-soft)" }),
  HANDLER: graphNodeStyle("var(--border-strong)", "neutral"),
  MAP: graphNodeStyle("var(--info)", "info", { background: "var(--info-soft)" }),
  WAIT: graphNodeStyle("var(--border-strong)", "neutral", { background: "var(--surface-sheet)" }),
  SCHEDULED: graphNodeStyle("var(--border-strong)", "neutral"),
  STARTED: graphNodeStyle("var(--info)", "info", { background: "var(--info-soft)" }),
  WAITING: graphNodeStyle("var(--warning)", "warning", { background: "var(--warning-soft)" }),
  SUCCEEDED: graphNodeStyle("var(--success)", "success", { background: "var(--success-soft)" }),
  FAILED: graphNodeStyle("var(--danger)", "danger", { background: "var(--danger-soft)" }),
  CANCELED: graphNodeStyle("var(--border-strong)", "neutral"),
  SKIPPED: graphNodeStyle("var(--border-subtle)", "neutral"),
} satisfies Record<WorkflowGraphNodeKind, GraphViewNodeStyle>;

export function workflowGraphNodes(
  steps: readonly WorkflowGraphStep[],
  statusByStep: ReadonlyMap<string, { status: string; detail?: React.ReactNode }> = new Map(),
  includePositions = true,
): GraphViewNode<WorkflowGraphNodeKind, { step: WorkflowGraphStep }>[] {
  return steps.map((step) => {
    const stepRun = statusByStep.get(step.id);
    const kind = workflowNodeKind(stepRun?.status ?? step.step_class);
    const title = step.name || step.key;
    const detail = stepRun ? stepRun.detail ?? stepRun.status : step.join_rule;
    return {
      id: step.id,
      kind,
      title,
      code: step.key,
      detail,
      ariaLabel: [title, step.key !== title ? step.key : null, typeof detail === "string" ? detail : null]
        .filter(Boolean).join(" · "),
      highlighted: step.is_entry,
      position: includePositions ? positionFromJson(step.position) : undefined,
      meta: { step },
    };
  });
}

export function workflowGraphEdges(
  edges: readonly WorkflowGraphEdge[],
): GraphViewEdge<WorkflowGraphEdgeKind, { edge: WorkflowGraphEdge }>[] {
  return edges.map((edge) => {
    const source = edge.source.name || edge.source.key;
    const target = edge.target.name || edge.target.key;
    return {
      id: edge.id,
      source: edge.source.id,
      target: edge.target.id,
      kind: edge.condition ? "condition" : "default",
      label: edge.condition || undefined,
      ariaLabel: [source, edge.condition || "continues", target].join(" · "),
      meta: { edge },
    };
  });
}

export function workflowNodeKind(value: string): WorkflowGraphNodeKind {
  const normalized = value.toUpperCase();
  if (normalized in workflowNodeStyles) {
    return normalized as WorkflowGraphNodeKind;
  }
  return "HANDLER";
}

function positionFromJson(value: unknown): GraphViewPosition | undefined {
  if (!value || typeof value !== "object") return undefined;
  const position = value as { x?: unknown; y?: unknown };
  if (typeof position.x !== "number" || typeof position.y !== "number") {
    return undefined;
  }
  return { x: position.x, y: position.y };
}

import type {
  GraphViewEdge,
  GraphViewNode,
  GraphViewNodeStyle,
  GraphViewPosition,
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
  AGENT: nodeStyle("var(--brand)", "brand"),
  GATE: nodeStyle("var(--warning)", "warning", "var(--warning-soft)"),
  HANDLER: nodeStyle("var(--border-strong)", "neutral"),
  MAP: nodeStyle("var(--info)", "info", "var(--info-soft)"),
  WAIT: nodeStyle("var(--border-strong)", "neutral", "var(--surface-sheet)"),
  SCHEDULED: nodeStyle("var(--border-strong)", "neutral"),
  STARTED: nodeStyle("var(--info)", "info", "var(--info-soft)"),
  WAITING: nodeStyle("var(--warning)", "warning", "var(--warning-soft)"),
  SUCCEEDED: nodeStyle("var(--success)", "success", "var(--success-soft)"),
  FAILED: nodeStyle("var(--danger)", "danger", "var(--danger-soft)"),
  CANCELED: nodeStyle("var(--border-strong)", "neutral"),
  SKIPPED: nodeStyle("var(--border-subtle)", "neutral"),
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

export function latestStepRunByStep(
  stepRuns: readonly WorkflowRunStepRun[],
): Map<string, WorkflowRunStepRun> {
  const latest = new Map<string, WorkflowRunStepRun>();
  for (const stepRun of stepRuns) {
    if (!stepRun.step) continue;
    latest.set(stepRun.step.id, stepRun);
  }
  return latest;
}

function nodeStyle(
  borderColor: string,
  badgeTone: GraphViewNodeStyle["badgeTone"],
  background?: string,
): GraphViewNodeStyle {
  return {
    width: 188,
    height: 76,
    borderColor,
    background,
    highlightedBorderColor: "var(--brand)",
    badgeTone,
  };
}

function positionFromJson(value: unknown): GraphViewPosition | undefined {
  if (!value || typeof value !== "object") return undefined;
  const position = value as { x?: unknown; y?: unknown };
  if (typeof position.x !== "number" || typeof position.y !== "number") {
    return undefined;
  }
  return { x: position.x, y: position.y };
}

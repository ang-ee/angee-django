import {
  type GraphViewEdge,
  type GraphViewEdgeStyle,
  type GraphViewNode,
  type GraphViewNodeStyle,
} from "@angee/ui";

export type NodeKind = "root" | "party" | "circle" | "identity" | "relationship_target";
export type EdgeKind =
  | "tie_weak"
  | "tie_medium"
  | "tie_strong"
  | "tie_fading"
  | "relationship"
  | "membership"
  | "identity";

export interface NodeMeta extends Record<string, unknown> {
  model?: string;
  record_id?: string;
  cadence?: { cadence_days?: number; touch_due_at?: string | null };
}

export interface EdgeMeta extends Record<string, unknown> {
  model?: string;
  record_id?: string;
  gravity?: number;
  is_fading?: boolean;
  kind_name?: string;
  kind_inverse_name?: string;
}

export type NexusNode = GraphViewNode<NodeKind, NodeMeta>;
export type NexusEdge = GraphViewEdge<EdgeKind, EdgeMeta>;

export const nodeStyles = {
  root: nodeStyle("var(--brand)", "var(--brand-soft)", "brand"),
  party: nodeStyle("var(--border-subtle)", undefined, "neutral"),
  circle: nodeStyle("var(--purple)", "var(--purple-soft)", "brand"),
  identity: nodeStyle("var(--info)", "var(--info-soft)", "info"),
  relationship_target: nodeStyle("var(--border-strong)", undefined, "neutral"),
} satisfies Record<NodeKind, GraphViewNodeStyle>;

export const edgeStyles = {
  tie_weak: { stroke: "var(--border-strong)", strokeWidth: 1 },
  tie_medium: { stroke: "var(--brand)", strokeWidth: 2 },
  tie_strong: { stroke: "var(--brand)", strokeWidth: 4 },
  tie_fading: { stroke: "var(--warning)", strokeWidth: 3 },
  relationship: { stroke: "var(--success)", strokeWidth: 2 },
  membership: { stroke: "var(--purple)", strokeWidth: 1.5 },
  identity: { stroke: "var(--info)", strokeWidth: 1 },
} satisfies Record<EdgeKind, GraphViewEdgeStyle>;

export function graphNodes(value: unknown): NexusNode[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!isObject(item) || typeof item.id !== "string" || typeof item.kind !== "string") return [];
    if (!isNodeKind(item.kind)) return [];
    return [{
      id: item.id,
      kind: item.kind,
      title: typeof item.title === "string" ? item.title : item.id,
      code: typeof item.code === "string" ? item.code : undefined,
      detail: typeof item.detail === "string" ? item.detail : undefined,
      meta: isObject(item.meta) ? item.meta as NodeMeta : undefined,
    }];
  });
}

export function graphEdges(value: unknown): NexusEdge[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!isObject(item) || typeof item.id !== "string" || typeof item.source !== "string" || typeof item.target !== "string") return [];
    const meta = isObject(item.meta) ? item.meta as EdgeMeta : undefined;
    return [{
      id: item.id,
      source: item.source,
      target: item.target,
      kind: edgeKind(typeof item.kind === "string" ? item.kind : "", meta),
      label: typeof item.label === "string" || typeof item.label === "number" ? String(item.label) : undefined,
      meta,
    }];
  });
}

export function isPartyNode(node: NexusNode): boolean {
  return node.meta?.model === "parties.Person" || node.meta?.model === "parties.Organization" || node.meta?.model === "parties.Party";
}

function edgeKind(kind: string, meta: EdgeMeta | undefined): EdgeKind {
  if (kind === "tie") {
    if (meta?.is_fading) return "tie_fading";
    const gravity = typeof meta?.gravity === "number" ? meta.gravity : 0;
    if (gravity >= 4) return "tie_strong";
    if (gravity >= 1.5) return "tie_medium";
    return "tie_weak";
  }
  if (kind === "relationship" || kind === "membership" || kind === "identity") return kind;
  return "relationship";
}

function nodeStyle(
  borderColor: string,
  background: string | undefined,
  badgeTone: GraphViewNodeStyle["badgeTone"],
): GraphViewNodeStyle {
  return { width: 190, height: 78, borderColor, background, badgeTone };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNodeKind(value: string): value is NodeKind {
  return value === "root" || value === "party" || value === "circle" || value === "identity" || value === "relationship_target";
}

import type { GraphViewEdge, GraphViewNode } from "@angee/ui";

import type {
  PlatformEdgeData,
  PlatformModelData,
} from "../documents";

export function modelGraphNodes(
  models: readonly PlatformModelData[],
  highlightId?: string | null,
): GraphViewNode<"model">[] {
  return models.map((model) => ({
    id: model.label,
    kind: "model",
    title: model.model_name,
    code: model.label,
    detail: model.addon_label,
    highlighted: highlightId ? model.label === highlightId : undefined,
  }));
}

export function modelGraphEdges(
  edges: readonly PlatformEdgeData[],
): GraphViewEdge[] {
  return edges.map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    kind: edge.kind,
    label: edge.field_name,
  }));
}

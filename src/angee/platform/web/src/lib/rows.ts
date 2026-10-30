import type { GraphViewEdge, GraphViewNode } from "@angee/ui";

import type {
  PlatformEdgeData,
  PlatformModelData,
} from "../documents";

export function modelGraphNodes(
  models: readonly PlatformModelData[],
  selectedId?: string | null,
): GraphViewNode<"model">[] {
  return models.map((model) => ({
    id: model.label,
    kind: "model",
    title: model.model_name,
    code: model.label,
    detail: model.addon_label,
    selected: selectedId ? model.label === selectedId : undefined,
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

import type { GraphViewEdge, GraphViewNode } from "@angee/base";

import type {
  PlatformAddonData,
  PlatformEdgeData,
  PlatformModelData,
} from "../documents";

export interface AddonRow extends Record<string, unknown> {
  id: string;
  addon: string;
  fullName: string;
  namespace: string;
  kind: string;
  models: number;
  fields: number;
  resources: number;
  dependsOn: string;
}

export function addonRows(addons: readonly PlatformAddonData[]): AddonRow[] {
  return addons.map((addon) => ({
    id: addon.id,
    addon: addon.label,
    fullName: addon.id,
    namespace: addon.namespace,
    kind: addon.kind,
    models: addon.modelCount,
    fields: addon.fieldCount,
    resources: addon.resourceCount,
    dependsOn: [...addon.dependsOn].join(", "),
  }));
}

export interface ModelRow extends Record<string, unknown> {
  id: string;
  model: string;
  addon: string;
  table: string;
  fields: number;
  relations: number;
  resourceType: string;
}

export function modelRows(models: readonly PlatformModelData[]): ModelRow[] {
  return models.map((model) => ({
    id: model.label,
    model: model.modelName,
    addon: model.addonLabel,
    table: model.dbTable,
    fields: model.fieldCount,
    relations: model.relationCount,
    resourceType: model.resourceType ?? "",
  }));
}

export interface FieldRow extends Record<string, unknown> {
  id: string;
  field: string;
  model: string;
  addon: string;
  kind: string;
  relationTarget: string;
}

export function fieldRows(models: readonly PlatformModelData[]): FieldRow[] {
  const rows: FieldRow[] = [];
  for (const model of models) {
    for (const field of model.fields) {
      rows.push({
        id: `${model.label}.${field.name}`,
        field: field.name,
        model: model.label,
        addon: field.addon,
        kind: field.kind,
        relationTarget: field.relationTarget ?? "",
      });
    }
  }
  return rows;
}

export function modelGraphNodes(
  models: readonly PlatformModelData[],
): GraphViewNode<"model">[] {
  return models.map((model) => ({
    id: model.label,
    kind: "model",
    title: model.modelName,
    code: model.label,
    detail: model.addonLabel,
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
    label: edge.fieldName,
  }));
}

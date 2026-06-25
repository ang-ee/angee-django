import type { GraphViewEdge, GraphViewNode } from "@angee/base";

import type {
  PlatformEdgeData,
  PlatformModelData,
} from "../documents";

function pushInto(index: Map<string, string[]>, key: string, value: string): void {
  const bucket = index.get(key);
  if (bucket) bucket.push(value);
  else index.set(key, [value]);
}

function sortedUnique(values: readonly string[]): string[] {
  return [...new Set(values)].sort();
}

export interface ModelRow extends Record<string, unknown> {
  id: string;
  model: string;
  addon: string;
  addonId: string;
  table: string;
  fields: number;
  relations: number;
  resourceType: string;
  dependsOn: string;
  dependsOnList: readonly string[];
  dependedBy: string;
  dependedByList: readonly string[];
}

export function modelRows(models: readonly PlatformModelData[]): ModelRow[] {
  const dependedBy = new Map<string, string[]>();
  for (const model of models) {
    for (const dep of model.depends_on) pushInto(dependedBy, dep, model.label);
  }
  return models.map((model) => {
    const dependsOnList = sortedUnique(model.depends_on);
    const dependedByList = sortedUnique(dependedBy.get(model.label) ?? []);
    return {
      id: model.label,
      model: model.model_name,
      addon: model.addon_label,
      addonId: model.addon_id,
      table: model.db_table,
      fields: model.field_count,
      relations: model.relation_count,
      resourceType: model.resource_type ?? "",
      dependsOn: dependsOnList.join(", "),
      dependsOnList,
      dependedBy: dependedByList.join(", "),
      dependedByList,
    };
  });
}

export interface FieldRow extends Record<string, unknown> {
  id: string;
  field: string;
  model: string;
  addon: string;
  addonId: string;
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
        addonId: model.addon_id,
        kind: field.kind,
        relationTarget: field.relation_target ?? "",
      });
    }
  }
  return rows;
}

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

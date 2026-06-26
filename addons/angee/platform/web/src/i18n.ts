import { useNamespaceT } from "@angee/ui";
import { type MessageVars } from "@angee/refine";

export const enPlatformMessages: Record<string, string> = {
  "platform.col.addon": "Addon",
  "platform.col.appLabel": "App label",
  "platform.col.dependedBy": "Depended by",
  "platform.col.dependsOn": "Depends on",
  "platform.col.field": "Field",
  "platform.col.fields": "Fields",
  "platform.col.graph": "Graph",
  "platform.col.hash": "Hash",
  "platform.col.kind": "Kind",
  "platform.col.model": "Model",
  "platform.col.models": "Models",
  "platform.col.namespace": "Namespace",
  "platform.col.relations": "Relations",
  "platform.col.relationTarget": "Relation target",
  "platform.col.resourceType": "Resource type",
  "platform.col.resources": "Resources",
  "platform.col.table": "Table",
  "platform.col.type": "Type",
  "platform.empty.addons": "No addons.",
  "platform.empty.fields": "No fields.",
  "platform.empty.models": "No models.",
  "platform.detail.addon.loading": "Loading addon…",
  "platform.detail.addon.notFound": "Addon not found",
  "platform.detail.dependencies": "Dependencies",
  "platform.detail.definition": "Definition",
  "platform.detail.model.loading": "Loading model…",
  "platform.detail.model.notFound": "Model not found",
  "platform.detail.open": "Open",
  "platform.detail.modelsWithCount": "Models ({count})",
};

export function usePlatformT(): (key: string, vars?: MessageVars) => string {
  return useNamespaceT("platform", enPlatformMessages);
}

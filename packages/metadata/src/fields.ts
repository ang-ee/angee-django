import {
  relationModelLabelForField,
  type DataResourceMetadata,
  type ModelFieldMetadata,
  type ModelMetadata,
} from "./artifact";
import type { QueryField } from "./query-schema";

/** A to-one relation explicitly projected by the node as a scalar identity. */
export function isScalarIdRelation(
  field: ModelFieldMetadata,
  model?: ModelMetadata | null,
): boolean {
  return field.kind === "relation" && field.relationObject === false
    && relationModelLabelForField(field, model) !== undefined;
}

/** Final field metadata owns relation classification for both projection shapes. */
export function isToOneRelationField(
  field: ModelFieldMetadata | undefined,
  _model?: ModelMetadata | null,
): boolean {
  return field?.kind === "relation";
}

const SCALAR_WIDGET: Readonly<Record<string, string>> = {
  Boolean: "switch",
  Int: "integer",
  Float: "float",
  Decimal: "float",
  DateTime: "datetime",
  Date: "date",
  JSON: "json",
};

export type ResourceFilterFieldType =
  | "boolean"
  | "date"
  | "datetime"
  | "number"
  | "selection"
  | "text";

export interface ChoiceFacetSupport {
  fieldName: string;
  field?: Pick<QueryField, "kind" | "scalar">;
  hasOptions?: boolean;
  hasTone?: boolean;
  allowStatusFallback?: boolean;
}

/**
 * The default widget family for a generated resource field. The backend owns the
 * widget vocabulary (`angee.graphql.data.field_classification`), so an explicit
 * `widget` — e.g. `"money"` over a Decimal scalar — wins; only a field with no
 * backend widget (a computed, model-less resource field) falls back to the
 * kind/scalar-derived default. UI owns the actual component registry.
 */
export function defaultWidgetForModelField(
  field: ModelFieldMetadata | undefined,
): string | undefined {
  if (!field) return undefined;
  if (field.widget) return field.widget;
  if (field.kind === "enum") return "select";
  if (field.kind === "relation") return "many2one";
  if (field.kind === "list") return "tagInput";
  return field.scalar ? SCALAR_WIDGET[field.scalar] : undefined;
}

export function filterFieldType(
  fieldName: string,
  field: Pick<QueryField, "kind" | "scalar"> | undefined,
  support: Omit<ChoiceFacetSupport, "fieldName" | "field"> = {},
): ResourceFilterFieldType | null {
  if (field?.kind === "enum") return "selection";
  if (field?.kind === "scalar" && field.scalar === "String") return "text";
  if (field?.kind === "scalar" && field.scalar === "Boolean") return "boolean";
  if (
    field?.kind === "scalar" &&
    (field.scalar === "Int" ||
      field.scalar === "Float" ||
      field.scalar === "Decimal")
  ) {
    return "number";
  }
  if (isDateField(field, fieldName)) {
    return field?.scalar === "Date" ? "date" : "datetime";
  }
  return supportsChoiceFacet({ fieldName, field, ...support }) ? "selection" : null;
}

/** Whether the resource's update root accepts writes for a field. */
export function fieldUpdatable(
  metadata: ModelMetadata | null | undefined,
  fieldName: string,
): boolean {
  if (!metadata?.resource.roots.update) return false;
  const updateFields = metadata.resource.updateFields;
  if (updateFields && !updateFields.includes(fieldName)) return false;
  return metadata.fields[fieldName]?.updatable !== false;
}

export function supportsChoiceFacet(support: ChoiceFacetSupport): boolean {
  if (support.field?.kind === "enum") return true;
  if (support.hasOptions) return true;
  if (support.hasTone) return true;
  return support.allowStatusFallback === true && support.fieldName === "status";
}

function looksLikeDateField(fieldName: string): boolean {
  const normalized = fieldName.toLowerCase();
  return normalized.endsWith("at") ||
    normalized.endsWith("_at") ||
    normalized.endsWith("date") ||
    normalized.endsWith("_date") ||
    normalized.endsWith("on") ||
    normalized.endsWith("_on");
}

/** Resolve date semantics from declared metadata, with a name fallback only when absent. */
export function isDateField(
  field: Pick<QueryField, "kind" | "scalar"> | undefined,
  fieldName: string,
): boolean {
  if (field) {
    return field.kind === "scalar" &&
      (field.scalar === "DateTime" || field.scalar === "Date");
  }
  return looksLikeDateField(fieldName);
}

/** Resolve an authored display path through its declared query sort capability. */
export function resourceOrderFieldForPath(
  path: string,
  resource: DataResourceMetadata | null | undefined,
): string | null {
  return resource ? resource.query.fields[path]?.sort?.field ?? null : path;
}

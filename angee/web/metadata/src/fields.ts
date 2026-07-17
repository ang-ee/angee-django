import type { ModelFieldMetadata, ModelMetadata } from "./artifact";

/**
 * A to-one relation the node projects as a bare `ID` scalar rather than a nested
 * object (`relationObject: false`): the wire carries the related row's public id
 * as a leaf, so a detail/form query selects it directly instead of emitting a
 * sub-selection the `ID` scalar would reject.
 */
export function isScalarIdRelation(field: ModelFieldMetadata): boolean {
  return field.kind === "scalar" && field.scalar === "ID" && Boolean(field.relationTarget);
}

/**
 * Is this field a to-one relation, whichever way the node projects it?
 *
 * The projection is a wire detail — an object sub-selection or a bare `ID` leaf —
 * not a different kind of fact. Both shapes name the same related model, carry the
 * same relation filter, and group by the same identity axis, so anything reasoning
 * about *relation-ness* must ask this rather than test `kind === "relation"` and
 * silently drop every scalar-id relation.
 */
export function isToOneRelationField(field: ModelFieldMetadata | undefined): boolean {
  if (!field) return false;
  return field.kind === "relation" || isScalarIdRelation(field);
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
  field?: ModelFieldMetadata;
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
  field: ModelFieldMetadata | undefined,
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
  if (field?.kind === "scalar" && field.scalar === "DateTime") return "datetime";
  if (field?.kind === "scalar" && field.scalar === "Date") return "date";
  if (looksLikeDateField(fieldName)) return "datetime";
  return supportsChoiceFacet({ fieldName, field, ...support }) ? "selection" : null;
}

/** Whether the resource's update root accepts writes for a field. */
export function fieldUpdatable(
  metadata: ModelMetadata | null | undefined,
  fieldName: string,
): boolean {
  if (!metadata?.rootFields?.update && !metadata?.resource?.roots.update) return false;
  const updateFields =
    metadata.rootFields?.updateFields ?? metadata.resource?.updateFields;
  if (updateFields && !updateFields.includes(fieldName)) return false;
  return metadata.fields[fieldName]?.updatable !== false;
}

export function supportsChoiceFacet(support: ChoiceFacetSupport): boolean {
  if (support.field?.kind === "enum") return true;
  if (support.hasOptions) return true;
  if (support.hasTone) return true;
  return support.allowStatusFallback === true && support.fieldName === "status";
}

export function looksLikeDateField(fieldName: string): boolean {
  const normalized = fieldName.toLowerCase();
  return normalized.endsWith("at") ||
    normalized.endsWith("_at") ||
    normalized.endsWith("date") ||
    normalized.endsWith("_date") ||
    normalized.endsWith("on") ||
    normalized.endsWith("_on");
}

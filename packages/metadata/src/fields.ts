import {
  isClientRowModel,
  relationModelLabelForField,
  type DataResourceMetadata,
  type ModelFieldMetadata,
  type ModelMetadata,
} from "./artifact";
import { resourceFieldPathToSnake } from "./naming";

/**
 * A to-one relation the node projects as a bare `ID` rather than a nested object.
 * Final metadata keeps relation semantics in `kind: "relation"` and records the
 * executable GraphQL shape as `relationObject: false`; older wire producers may
 * still describe the same leaf as a scalar `ID` with a canonical relation target.
 */
export function isScalarIdRelation(
  field: ModelFieldMetadata,
  model?: ModelMetadata | null,
): boolean {
  if (!relationModelLabelForField(field, model)) return false;
  return (field.kind === "relation" && field.relationObject === false)
    || (field.kind === "scalar" && field.scalar === "ID");
}

/**
 * Is this field a to-one relation, whichever way the node projects it?
 *
 * The projection is a wire detail — an object sub-selection or a bare `ID` leaf.
 * Both shapes name the same related model, carry the same relation filter, and
 * group by the same identity axis. The scalar branch below remains for older
 * metadata whose coarse kind predates the finalized relation classifier.
 */
export function isToOneRelationField(
  field: ModelFieldMetadata | undefined,
  model?: ModelMetadata | null,
): boolean {
  if (!field) return false;
  return field.kind === "relation" || isScalarIdRelation(field, model);
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
  field: ModelFieldMetadata | undefined,
  fieldName: string,
): boolean {
  if (field) {
    return field.kind === "scalar" &&
      (field.scalar === "DateTime" || field.scalar === "Date");
  }
  return looksLikeDateField(fieldName);
}

/**
 * Resolve a displayed field path to an explicitly declared server order field.
 * Hasura order inputs use flat Django paths; nested display accessors stay dotted
 * in Table and map only when metadata declares that exact path. Local rows have
 * no server order-input restriction. Absent resource metadata preserves authored
 * local table behavior rather than inventing a schema capability.
 */
export function resourceOrderFieldForPath(
  path: string,
  resource: DataResourceMetadata | null | undefined,
): string | null {
  if (!resource || isClientRowModel(resource)) return path;
  if (resource.orderFields.includes(path)) return path;
  const normalized = resourceFieldPathToSnake(path.replaceAll(".", "__"));
  return resource.orderFields.find((field) => resourceFieldPathToSnake(field) === normalized) ?? null;
}

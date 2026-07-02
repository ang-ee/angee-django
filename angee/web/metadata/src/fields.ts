import type { ModelFieldMetadata } from "./artifact";

const SCALAR_WIDGET: Readonly<Record<string, string>> = {
  Boolean: "switch",
  Int: "integer",
  Float: "float",
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
 * The default widget family for a generated resource field. Metadata owns the
 * field kind/scalar classification; UI owns the actual component registry.
 */
export function defaultWidgetForModelField(
  field: ModelFieldMetadata | undefined,
): string | undefined {
  if (!field) return undefined;
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
  if (field?.kind === "scalar" && (field.scalar === "Int" || field.scalar === "Float")) {
    return "number";
  }
  if (field?.kind === "scalar" && field.scalar === "DateTime") return "datetime";
  if (field?.kind === "scalar" && field.scalar === "Date") return "date";
  if (looksLikeDateField(fieldName)) return "datetime";
  return supportsChoiceFacet({ fieldName, field, ...support }) ? "selection" : null;
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

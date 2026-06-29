import type {
  ModelMetadata,
  ModelRelationFilterMetadata,
  SchemaFieldMetadata,
} from "@angee/resources";
import type {
  ReactNode } from "react";
import type {
  ModelFieldMetadata,
} from "@angee/resources";

import type { WidgetOption } from "../widgets";
import type {
  ColumnDescriptor,
  FieldDescriptor,
} from "./page";
import { titleCase } from "../lib/titleCase";
import { enumValueLabel, groupFieldLabel } from "./ListInternals";

/** A form field's resolved relation target — which model the picker lists, its
 * display field, and whether the related model can be created inline. */
export interface RelationFieldInfo {
  /** Related model label, e.g. `"Vendor"`. */
  resource: string;
  /** Field shown as the option label. */
  labelField: string;
  /** A create mutation exists for the related model. */
  canCreate: boolean;
  /** Filter shape accepted by the current model's filter input for this relation. */
  filter?: ModelRelationFilterMetadata;
}

// Server-owned fields a create form never edits. These are GraphQL wire field
// names, which are snake_case (the schema's Hasura naming) — match what the
// resource metadata exposes as `field.name`.
const NON_EDITABLE_FIELDS = new Set([
  "id",
  "created_at",
  "updated_at",
  "created_by",
  "created_by_label",
  "updated_by",
  "updated_by_label",
]);

const SCALAR_WIDGET: Readonly<Record<string, string>> = {
  Boolean: "switch",
  Int: "integer",
  Float: "float",
  DateTime: "datetime",
  Date: "date",
  JSON: "json",
};

/**
 * The default widget for a field from its resource metadata: enums pick a select,
 * object relations a `many2one` picker, string-list fields a tag input, and
 * scalars map by GraphQL scalar (Boolean→switch, Int→integer, …). Returns
 * `undefined` for a plain string scalar (the FormView text fallback). Shared by
 * both the declared-field path (`fieldsWithMetadataDefaults`) and the inline
 * relation-create path (`formFieldDescriptor`) so a field resolves the same
 * widget wherever it is rendered.
 */
export function defaultWidgetFor(
  field: ModelFieldMetadata | undefined,
): string | undefined {
  if (!field) return undefined;
  if (field.kind === "enum") return "select";
  if (field.kind === "relation") return "many2one";
  if (field.kind === "list") return "tagInput";
  return field.scalar ? SCALAR_WIDGET[field.scalar] : undefined;
}

/**
 * Resolve a form field to its relation target, or `null` when it is not an
 * object relation whose related model is listable. Only nested object fields
 * (`kind: "relation"`) qualify — a bare `ID` scalar FK is opaque to resource
 * metadata, so it stays an explicitly-configured field.
 */
export function relationFieldInfo(
  fieldName: string,
  modelMetadata: ModelMetadata | null,
  schemaMetadata: SchemaFieldMetadata,
): RelationFieldInfo | null {
  const field = modelMetadata?.fields[fieldName];
  if (field?.kind !== "relation" || !field.relationTarget) return null;
  const related = schemaMetadata.types[field.relationTarget];
  // Without a list root field the picker has no way to fetch options.
  if (!related?.rootFields?.list) return null;
  return {
    resource: stripTypeSuffix(field.relationTarget),
    labelField: related.recordRepresentation ?? "id",
    canCreate: Boolean(related.rootFields.create),
    ...(field.relationFilter ? { filter: field.relationFilter } : {}),
  };
}

/**
 * Editable fields for a model's inline create form, derived from its metadata:
 * scalars, enums, and object relations, minus `id` and audit fields. Used by the
 * relation picker's create dialog so an object relation can be created inline
 * with no per-consumer field list.
 */
export function formFieldsFromMetadata(
  metadata: ModelMetadata | null,
): FieldDescriptor[] {
  if (!metadata) return [];
  const fields: FieldDescriptor[] = [];
  for (const field of Object.values(metadata.fields)) {
    if (NON_EDITABLE_FIELDS.has(field.name) || field.kind === "list") continue;
    fields.push(formFieldDescriptor(field));
  }
  return fields;
}

function formFieldDescriptor(field: ModelFieldMetadata): FieldDescriptor {
  const widget = defaultWidgetFor(field);
  return widget ? { name: field.name, widget } : { name: field.name };
}

function stripTypeSuffix(typeName: string): string {
  return typeName.endsWith("Type") ? typeName.slice(0, -4) : typeName;
}

const ENUM_OPTION_WIDGETS = new Set([
  "select",
  "selection",
  "statusbar",
  "statusBadge",
  "colorDot",
  "ribbon",
]);

/** Apply metadata-derived column labels and enum options without overriding props. */
export function columnsWithMetadataDefaults<TRow extends object>(
  columns: readonly ColumnDescriptor<TRow>[],
  metadata: ModelMetadata | null,
): readonly ColumnDescriptor<TRow>[] {
  return columns.map((column) => {
    const field = metadata?.fields[column.field];
    const options = enumOptions(field);
    return {
      ...column,
      header: fieldLabel(column.field, field, column.header),
      ...(column.options === undefined
        && isEnumOptionWidget(column.widget)
        && options.length > 0
        ? { options }
        : {}),
    };
  });
}

/** Apply metadata-derived field labels and enum options without overriding props. */
export function fieldsWithMetadataDefaults(
  fields: readonly FieldDescriptor[],
  metadata: ModelMetadata | null,
): readonly FieldDescriptor[] {
  return fields.map((field) => {
    const fieldMetadata = metadata?.fields[field.name];
    // A declared field with no explicit widget inherits the metadata-derived default
    // for its kind/scalar: enum→select, relation→many2one (selecting `<field>.id`
    // for the picker), Boolean→switch, list→tagInput, etc. Without this every
    // bare `<Field>` falls to the text widget — booleans then submit "" and fail.
    const widget =
      field.widget === undefined && field.options === undefined
        ? defaultWidgetFor(fieldMetadata)
        : field.widget;
    const options = enumOptions(fieldMetadata);
    return {
      ...field,
      ...(widget !== field.widget ? { widget } : {}),
      label: fieldLabel(field.name, fieldMetadata, field.label),
      ...(field.options === undefined
        && isEnumOptionWidget(widget ?? field.kind)
        && options.length > 0
        ? { options }
        : {}),
    };
  });
}

/** Resolve a field label from explicit props, resource metadata, then title-case. */
export function fieldLabel(
  name: string,
  metadata: ModelFieldMetadata | undefined,
  explicit?: ReactNode,
): ReactNode {
  return explicit ?? metadata?.label ?? titleCase(name);
}

/** Resolve a group label from resource metadata, then group-specific field text. */
export function groupLabel(
  name: string,
  metadata: ModelFieldMetadata | undefined,
): string {
  return metadata?.label ?? groupFieldLabel(name);
}

/** Return enum widget options for a metadata field, or an empty list. */
export function enumOptions(
  field: ModelFieldMetadata | undefined,
): readonly WidgetOption[] {
  if (field?.kind !== "enum" && field?.kind !== "list") return [];
  return field.values?.map((value) => ({
    value: value.value,
    label: enumValueLabel(value),
  })) ?? [];
}

function isEnumOptionWidget(widget: string | undefined): boolean {
  return widget !== undefined && ENUM_OPTION_WIDGETS.has(widget);
}

import type {
  ModelMetadata,
  ModelRelationFilterMetadata,
  SchemaFieldMetadata,
} from "@angee/metadata";
import { defaultWidgetForModelField } from "@angee/metadata";
import type { ReactNode } from "react";
import type { ModelFieldMetadata } from "@angee/metadata";

import type { WidgetOption } from "../widgets";
import type { ColumnDescriptor, FieldDescriptor } from "./page";
import { titleCase } from "../lib/titleCase";
import { enumValueLabel, groupFieldLabel } from "./resource-view-list-body";

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
  return defaultWidgetForModelField(field);
}

/**
 * Resolve a form field to its relation target, or `null` when it carries no
 * listable relation. Two field shapes qualify: a nested object relation
 * (`kind: "relation"`), and a to-one FK the node projects as a bare `ID` scalar
 * ({@link isScalarIdRelation}). The scalar-id form reads/writes the flat id and the
 * detail/form query selects it as a leaf (never an object sub-selection); the
 * object form reads the nested `{ id }`. Both wire the same relation picker and
 * label through the relation metadata.
 */
export function relationFieldInfo(
  fieldName: string,
  modelMetadata: ModelMetadata | null,
  schemaMetadata: SchemaFieldMetadata,
): RelationFieldInfo | null {
  const field = modelMetadata?.fields[fieldName];
  if (!field || (field.kind !== "relation" && !isScalarIdRelation(field))) return null;
  return resolveRelationTarget(field, schemaMetadata);
}

/**
 * A to-one relation the node projects as a bare `ID` scalar (`kind: "scalar"`,
 * `scalar: "ID"`, carrying a `relationTarget`). The backend classifies a
 * `CompanyScopedMixin.company` (and any FK projected as `ID!` rather than a nested
 * object) this way, with a `select` scalar-id widget — so the detail/form query
 * selects it as a leaf instead of emitting a sub-selection the wire `ID` rejects,
 * while the relation picker still resolves through the relation metadata.
 */
export function isScalarIdRelation(field: ModelFieldMetadata): boolean {
  return field.kind === "scalar" && field.scalar === "ID" && Boolean(field.relationTarget);
}

/**
 * The to-many analog of {@link relationFieldInfo}: an M2M child field
 * (`kind: "list"`) whose `relationModelLabel` resolved to a listable related
 * model. `EditableLines` renders it as a multi-select of related rows and
 * persists the picked public ids. A `kind: "list"` field with no relation target
 * (a plain string/array column) stays a tag input.
 */
export function relationListFieldInfo(
  fieldName: string,
  modelMetadata: ModelMetadata | null,
  schemaMetadata: SchemaFieldMetadata,
): RelationFieldInfo | null {
  const field = modelMetadata?.fields[fieldName];
  if (field?.kind !== "list") return null;
  return resolveRelationTarget(field, schemaMetadata);
}

/**
 * Resolve a relation-carrying field to its listable target — shared by the to-one
 * ({@link relationFieldInfo}) and to-many ({@link relationListFieldInfo})
 * resolvers, which differ only in the field kind they accept. Returns `null` when
 * the field has no relation target or the target exposes no list root (so the
 * picker would have no way to fetch options).
 */
function resolveRelationTarget(
  field: ModelFieldMetadata,
  schemaMetadata: SchemaFieldMetadata,
): RelationFieldInfo | null {
  if (!field.relationTarget) return null;
  const related = schemaMetadata.types[field.relationTarget];
  if (!related?.rootFields?.list) return null;
  return {
    resource: stripTypeSuffix(field.relationTarget),
    labelField: related.recordRepresentation ?? "id",
    canCreate: Boolean(related.rootFields.create),
    ...(field.relationFilter ? { filter: field.relationFilter } : {}),
  };
}

/**
 * Build a relation target for an explicitly-named resource — an action arg's
 * relation picker, which names its target model rather than deriving it from a
 * parent model field. Returns `null` when the resource exposes no list root (so
 * the picker has no way to fetch options). Mirrors {@link relationFieldInfo}, but
 * keyed on the resolved model metadata instead of a parent field.
 */
export function relationFieldInfoForResource(
  resource: string,
  model: ModelMetadata | null,
): RelationFieldInfo | null {
  if (!model?.rootFields?.list) return null;
  return {
    resource,
    labelField: model.recordRepresentation ?? "id",
    canCreate: Boolean(model.rootFields.create),
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
  schemaMetadata?: SchemaFieldMetadata,
): readonly ColumnDescriptor<TRow>[] {
  return columns.map((column) => {
    const field = metadata?.fields[column.field];
    const options = enumOptions(field);
    // A bare relation column (`<Column field="product">`) reads a GraphQL object
    // type, which cannot be selected as a leaf. Resolve it to its related type's
    // label path (`product.<recordRepresentation>`, e.g. `product.display_name`) —
    // the same relation read-expansion the form (`addFieldSelection`) and F6 child
    // lines (`lineReadSelectionPaths`) already do — so the read selects
    // `{ product { display_name } }` and the cell renders the label. The relation's
    // own name still keys grouping via the dotted-path group option.
    const relationLabelField = relationColumnLabelField(column, field, schemaMetadata);
    return {
      ...column,
      ...(relationLabelField ? { field: relationLabelField } : {}),
      header: fieldLabel(column.field, field, column.header),
      // A bare column inherits the backend's explicit widget (e.g. `"money"` over a
      // Decimal), so its cell renders through the registered widget instead of the raw
      // scalar. Only the explicit backend widget is inherited — kind/scalar-derived
      // defaults stay out, because list cells render enums, relations, and plain
      // scalars natively (unlike a form, which needs an edit widget per field). A
      // relation resolved to its label path renders the scalar label as text, so it
      // drops the relation's `many2one` edit widget.
      ...(!relationLabelField && column.widget === undefined && field?.widget
        ? { widget: field.widget }
        : {}),
      ...(column.currencyField === undefined && field?.currencyField
        ? { currencyField: field.currencyField }
        : {}),
      ...(column.options === undefined &&
      isEnumOptionWidget(column.widget) &&
      options.length > 0
        ? { options }
        : {}),
    };
  });
}

/**
 * The label path a bare relation column reads for display: `<field>.<labelField>`,
 * where `labelField` is the related type's `recordRepresentation` (a readable
 * scalar) or `id` when the type declares none. Returns `null` for a non-relation
 * column, an already-dotted path, or a column that pins its own `render`/`widget`
 * (those own their value shape). Only a relation projected as a nested object
 * (`relationObject`) can be sub-selected — a FK projected as a public-id scalar
 * (`location: ID`) stays a leaf, so it is left untouched. Without this a
 * nested-relation column selects the whole object as a leaf — a GraphQL error.
 */
function relationColumnLabelField<TRow extends object>(
  column: ColumnDescriptor<TRow>,
  field: ModelFieldMetadata | undefined,
  schemaMetadata: SchemaFieldMetadata | undefined,
): string | null {
  if (column.render || column.widget !== undefined || column.field.includes(".")) {
    return null;
  }
  if (field?.kind !== "relation" || !field.relationTarget || field.relationObject !== true) {
    return null;
  }
  const rep = schemaMetadata?.types[field.relationTarget]?.recordRepresentation;
  return `${column.field}.${rep && rep !== "id" ? rep : "id"}`;
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
      ...(field.currencyField === undefined && fieldMetadata?.currencyField
        ? { currencyField: fieldMetadata.currencyField }
        : {}),
      ...(field.options === undefined &&
      isEnumOptionWidget(widget ?? field.kind) &&
      options.length > 0
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

/** Resolve a grouping-field label from resource metadata, then field text. */
export function resourceFieldGroupLabel(
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
  return (
    field.values?.map((value) => ({
      value: value.value,
      label: enumValueLabel(value),
    })) ?? []
  );
}

function isEnumOptionWidget(widget: string | undefined): boolean {
  return widget !== undefined && ENUM_OPTION_WIDGETS.has(widget);
}

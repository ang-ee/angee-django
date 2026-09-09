import type { ReactNode } from "react";
import { get, set } from "react-hook-form";
import {
  publicIdLabel,
  relationModelLabelForField,
  rowPublicId,
  rowValueAtPath,
  type DataResourceSubtitleMetadata,
  type ModelFieldMetadata,
  type ModelMetadata,
  type Row,
} from "@angee/metadata";

import { dateFromUnknown, formatDate } from "../../widgets/date-format";
import { canonicalOptionValue, relationValueId } from "../../widgets/types";
import type { UiTranslate } from "../../i18n";
import {
  fieldWidgetId,
  isRelationIdField,
  type FieldDescriptor,
  type GroupDescriptor,
} from "../page";
import type { RelationFieldInfo } from "../resource/model-metadata-defaults";
import { isStructuredPresenceField, structuredFieldErrorPaths } from "./field-values";

export type FormValues = Record<string, unknown>;
const MISSING_DOTTED_VALUE = Symbol("missing-dotted-value");

/** Child lines threaded through a form reset alongside declared field values. */
export interface LinesSeed {
  field: string;
  rows: readonly Row[];
}

export interface FormSectionModel {
  key: string;
  label?: ReactNode;
  icon?: ReactNode;
  badge?: ReactNode;
  columns?: number;
  collapsible?: boolean;
  defaultOpen?: boolean;
  fields: readonly FieldDescriptor[];
  render?: () => ReactNode;
  sequence?: number;
  order?: number;
}

export interface FormViewFieldLayout {
  titleField: FieldDescriptor | undefined;
  statusField: FieldDescriptor | undefined;
  bodyField: FieldDescriptor | undefined;
  gridFields: readonly FieldDescriptor[];
  gridGroups: readonly GroupDescriptor[];
}

export function formSections(
  fields: readonly FieldDescriptor[],
  groups: readonly GroupDescriptor[],
  sequences: readonly (number | undefined)[] = [],
): readonly FormSectionModel[] {
  if (groups.length === 0) return [{ key: "fields", fields }];
  const groupedNames = new Set<string>();
  const sections: FormSectionModel[] = groups.flatMap((group, index) => {
    if (group.fields.length === 0) return [];
    for (const field of group.fields) groupedNames.add(field.name);
    return [
      {
        key: `group:${index}:${String(group.label ?? "")}`,
        label: group.label,
        columns: group.columns,
        collapsible: group.collapsible,
        defaultOpen: group.defaultOpen,
        fields: group.fields,
        sequence: sequences[index],
        order: index,
      },
    ];
  });
  const ungrouped = fields.filter((field) => !groupedNames.has(field.name));
  if (ungrouped.length > 0) sections.unshift({ key: "fields", fields: ungrouped });
  return sections;
}

/** Classify fields rendered in record chrome versus the section grid/body. */
export function formViewFieldLayout(
  formFields: readonly FieldDescriptor[],
  resolvedFields: readonly FieldDescriptor[],
  resolvedGroups: readonly GroupDescriptor[],
  metadata: ModelMetadata | null,
): FormViewFieldLayout {
  const titleField = titleFieldFor(formFields, metadata);
  const statusField = formFields.find(
    (field) => fieldWidgetId(field) === "statusbar" && !field.showWhen,
  );
  const bodyField = bodyFieldFor(formFields, titleField, statusField);
  const excluded = new Set(
    [titleField?.name, statusField?.name, bodyField?.name].filter(
      (name): name is string => name !== undefined,
    ),
  );
  return {
    titleField,
    statusField,
    bodyField,
    gridFields: resolvedFields.filter((field) => !excluded.has(field.name)),
    gridGroups: resolvedGroups.map((group) => ({
      ...group,
      fields: group.fields.filter((field) => !excluded.has(field.name)),
    })),
  };
}

function titleFieldFor(
  fields: readonly FieldDescriptor[],
  metadata: ModelMetadata | null,
): FieldDescriptor | undefined {
  const stable = fields.filter((field) => !field.showWhen);
  return (
    stable.find((field) => field.title) ??
    stable.find((field) => field.name === metadata?.resource.recordRepresentation) ??
    stable.find((field) => field.name === "title")
  );
}

function bodyFieldFor(
  fields: readonly FieldDescriptor[],
  titleField: FieldDescriptor | undefined,
  statusField: FieldDescriptor | undefined,
): FieldDescriptor | undefined {
  const candidates = fields.filter(
    (field) =>
      field.body !== false &&
      !field.showWhen &&
      field.name !== titleField?.name &&
      field.name !== statusField?.name,
  );
  return (
    candidates.find((field) => field.body) ??
    candidates.find(isNamedBodyField) ??
    candidates.find(isLongTextField)
  );
}

function isNamedBodyField(field: FieldDescriptor): boolean {
  const name = normaliseFieldName(field.name);
  return name === "body" || name === "description";
}

function isLongTextField(field: FieldDescriptor): boolean {
  const id = fieldWidgetId(field);
  return (
    id === "textarea" ||
    id === "markdown" ||
    id === "markdown.editor" ||
    id === "markdown.preview"
  );
}

export function recordRepresentationValue(
  record: Row | null | undefined,
  metadata: ModelMetadata | null,
): unknown {
  const field = metadata?.resource.recordRepresentation;
  if (!record || !field) return undefined;
  return (record as Record<string, unknown>)[field];
}

export function titleText(value: unknown, fallback: string): string {
  const text = String(value ?? "").trim();
  return text || fallback;
}

export function addFieldSelection(
  paths: Set<string>,
  field: FieldDescriptor,
  relation?: RelationFieldInfo,
  metadata?: ModelFieldMetadata,
): void {
  if (
    isRelationIdField(field)
    && (
      metadata === undefined
      || (metadata.kind === "relation" && metadata.relationObject === true)
    )
  ) {
    paths.add(`${field.name}.id`);
    if (relation && relation.labelField !== "id") {
      paths.add(`${field.name}.${relation.labelField}`);
    }
    return;
  }
  paths.add(field.name);
}

export function withModeLockedFields(
  fields: readonly FieldDescriptor[],
  isCreate: boolean,
): readonly FieldDescriptor[] {
  return fields.map((field) => {
    const locked = isCreate ? field.editOnly : field.createOnly;
    return locked && !field.readOnly ? { ...field, readOnly: true } : field;
  });
}

export function flattenedFormFields(
  fields: readonly FieldDescriptor[],
  groups: readonly GroupDescriptor[],
): readonly FieldDescriptor[] {
  const seen = new Set<string>();
  const flattened: FieldDescriptor[] = [];
  for (const field of fields) addFormField(flattened, seen, field);
  for (const group of groups) {
    for (const field of group.fields) addFormField(flattened, seen, field);
  }
  return flattened;
}

function addFormField(
  fields: FieldDescriptor[],
  seen: Set<string>,
  field: FieldDescriptor,
): void {
  if (seen.has(field.name)) return;
  seen.add(field.name);
  fields.push(field);
}

export function emptyDraft(
  fields: readonly FieldDescriptor[],
  defaultValues?: Record<string, unknown>,
): FormValues {
  const evaluation: FormValues = {};
  for (const field of fields) {
    const value = initialDraftFieldValue(field, defaultValues);
    if (value !== MISSING_DOTTED_VALUE) setDottedValue(evaluation, field.name, cloneFormValue(value));
  }
  const draft: FormValues = {};
  for (const declared of fields) {
    const field = resolveField(declared, evaluation);
    if (!isFieldVisible(field, evaluation)) continue;
    const value = initialDraftFieldValue(field, defaultValues);
    if (value !== MISSING_DOTTED_VALUE) setDottedValue(draft, field.name, cloneFormValue(value));
  }
  return draft;
}

function initialDraftFieldValue(
  field: FieldDescriptor,
  defaultValues: Record<string, unknown> | undefined,
): unknown {
  if (hasDottedValue(defaultValues, field.name)) return dottedValue(defaultValues, field.name);
  if (field.hasDefault || field.defaultValue !== undefined) return field.defaultValue;
  if (field.omittable || field.presenceRequired) return MISSING_DOTTED_VALUE;
  return emptyValue(field);
}

export function recordToValues(
  record: Row,
  fields: readonly FieldDescriptor[],
  lines?: LinesSeed,
): FormValues {
  const values: FormValues = {};
  for (const declared of fields) {
    const field = resolveField(declared, record);
    if (!isFieldVisible(field, record)) continue;
    const present = hasDottedValue(record, field.name);
    if (!present && (field.omittable || field.presenceRequired)) continue;
    const raw = dottedValue(record, field.name);
    const value = isRelationIdField(field)
      ? raw ?? null
      : recordFieldValue({ ...record, [field.name]: raw }, field);
    setDottedValue(values, field.name, value === undefined ? emptyValue(field) : value);
  }
  if (lines) values[lines.field] = lines.rows;
  return values;
}

export function baselineLineRows(
  baseline: FormValues,
  field: string,
  fallback: readonly Row[] | null,
): readonly Row[] {
  const rows = baseline[field];
  return Array.isArray(rows) ? (rows as readonly Row[]) : fallback ?? [];
}

function recordFieldValue(record: Row, field: FieldDescriptor): unknown {
  const value = record[field.name];
  const optionValue = canonicalOptionValue(field.options, value);
  if (optionValue !== undefined) return optionValue;
  if (!isRelationIdField(field)) return value;
  if (typeof value === "string") return value;
  if (isRecord(value)) return rowPublicId(value) ?? value;
  return value;
}

export function isFieldVisible(field: FieldDescriptor, values: FormValues): boolean {
  return !field.showWhen || field.showWhen(values);
}

export function missingRequiredFieldNames(
  values: FormValues,
  fields: readonly FieldDescriptor[],
  requiredFieldNames: ReadonlySet<string>,
): readonly string[] {
  return fields.flatMap((declared) => {
    const field = resolveField(declared, values);
    if (!isFieldVisible(field, values)) return [];
    const value = dottedValue(values, field.name);
    if (isStructuredPresenceField(field)) {
      return structuredFieldErrorPaths(field, value, hasDottedValue(values, field.name));
    }
    return (field.required || requiredFieldNames.has(field.name)) && isEmptyFieldValue(value)
      ? [field.name]
      : [];
  });
}

export function visibleSections(
  sections: readonly FormSectionModel[],
  values: FormValues,
): readonly FormSectionModel[] {
  return sections.map((section) => ({
    ...section,
    fields: section.fields
      .map((field) => resolveField(field, values))
      .filter((field) => isFieldVisible(field, values)),
  }));
}

export function mutationData(
  values: FormValues,
  fields: readonly FieldDescriptor[],
  options: {
    dirtyFields: Record<string, unknown>;
    id?: string | null;
    isCreate: boolean;
    fieldMetadata?: Readonly<Record<string, ModelFieldMetadata>> | null;
    seededFieldNames?: ReadonlySet<string> | null;
    writableFields?: ReadonlySet<string> | null;
  },
): FormValues {
  const data: FormValues = {};
  for (const declared of fields) {
    const field = resolveField(declared, values);
    if (options.writableFields && !isWritableDottedField(options.writableFields, field.name)) continue;
    const seededDefault =
      options.isCreate &&
      !field.editOnly &&
      (field.defaultValue !== undefined ||
        (options.seededFieldNames?.has(field.name) ?? false));
    if (field.readOnly && !seededDefault) continue;
    if (!isFieldVisible(field, values)) continue;
    const next = mutationFieldValue(field, dottedValue(values, field.name));
    const dirty = Boolean(dottedValue(options.dirtyFields, field.name));
    const structuredRootDirty = field.name.includes(".") && isDirtyValue(
      dottedValue(options.dirtyFields, field.name.split(".", 1)[0] ?? field.name),
    );
    if (
      options.isCreate
      && isBlankCreateValue(
        field,
        options.fieldMetadata?.[field.name],
        next,
        {
          dirty,
          seeded: seededDefault,
        },
      )
    ) {
      continue;
    }
    if (!options.isCreate && !dirty && !structuredRootDirty) continue;
    setDottedValue(data, field.name, next);
  }
  if (!options.isCreate && options.id != null) data.id = options.id;
  return data;
}

export function resolveField(field: FieldDescriptor, values: FormValues): FieldDescriptor {
  const resolved = field.resolve?.(values);
  return resolved ? { ...field, ...resolved } : field;
}

function isWritableDottedField(writable: ReadonlySet<string>, path: string): boolean {
  return writable.has(path) || writable.has(path.split(".", 1)[0] ?? path);
}

function isDirtyValue(value: unknown): boolean {
  if (value === true) return true;
  if (!value || typeof value !== "object") return false;
  return Object.values(value).some(isDirtyValue);
}

function hasDottedValue(value: unknown, path: string): boolean {
  return get(value, path, MISSING_DOTTED_VALUE) !== MISSING_DOTTED_VALUE;
}

function dottedValue(value: unknown, path: string): unknown {
  return get(value, path);
}

function setDottedValue(target: Record<string, unknown>, path: string, value: unknown): void {
  set(target, path, value);
}

function cloneFormValue(value: unknown): unknown {
  if (value == null || typeof value !== "object") return value;
  return structuredClone(value);
}

function mutationFieldValue(field: FieldDescriptor, value: unknown): unknown {
  if (isRelationIdField(field)) return relationValueId(value);
  return value;
}

function emptyValue(field: FieldDescriptor): unknown {
  if (isNumericField(field)) return null;
  if (isNullableScalarWidget(field)) return null;
  if (field.widget === "tagInput") return [];
  if (field.kind === "switch" || field.widget === "switch") return false;
  if (fieldWidgetId(field) === "json") return {};
  return "";
}

function isEmptyFieldValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

function isNullableScalarWidget(field: FieldDescriptor): boolean {
  const id = fieldWidgetId(field);
  return id === "date" || id === "datetime";
}

function isNumericField(field: FieldDescriptor): boolean {
  const id = fieldWidgetId(field);
  return id === "integer" || id === "float";
}

function hasOptionValue(field: FieldDescriptor): boolean {
  return Boolean(
    field.options &&
      (field.widget === "select" ||
        field.widget === "many2one" ||
        field.widget === "statusbar" ||
        field.kind === "select" ||
        field.kind === "selection"),
  );
}

function isBlankCreateValue(
  field: FieldDescriptor,
  metadata: ModelFieldMetadata | undefined,
  value: unknown,
  intent: { dirty: boolean; seeded: boolean },
): boolean {
  if (value == null) {
    if (!intent.dirty && !intent.seeded) return true;
    return !isStringScalar(metadata);
  }
  if (value !== "") return false;
  if (isRelationIdField(field) || (metadata && relationModelLabelForField(metadata))) return true;
  if (metadata) {
    return metadata.kind === "enum" || !isStringScalar(metadata);
  }
  return (
    hasOptionValue(field)
    || isNumericField(field)
    || isNullableScalarWidget(field)
  );
}

function isStringScalar(metadata: ModelFieldMetadata | undefined): boolean {
  return metadata?.kind === "scalar" && metadata.scalar === "String";
}

function isRecord(value: unknown): value is Row {
  return Boolean(value) && typeof value === "object";
}

export function fieldAriaLabel(field: FieldDescriptor): string {
  return typeof field.label === "string" ? field.label : field.name;
}

export function gridFieldClass(field: FieldDescriptor): string | undefined {
  return fieldWidgetId(field) === "tagInput" ? "col-span-full" : undefined;
}

export function fieldErrorMessages(errors: readonly unknown[], path?: string): string[] {
  return errors.flatMap((error) => nestedFieldErrorMessages(error, path));
}

function nestedFieldErrorMessages(error: unknown, path?: string): string[] {
  if (error && typeof error === "object" && "message" in error
    && (typeof error.message === "string" || typeof error.message === "number")) {
    const message = String(error.message);
    return path ? [`${path}: ${message}`] : [message];
  }
  if (!error || typeof error !== "object") return [fieldErrorMessage(error)];
  return Object.entries(error).flatMap(([name, child]) => {
    if (name === "ref" || name === "type" || child === undefined) return [];
    return nestedFieldErrorMessages(child, path ? `${path}.${name}` : name);
  });
}

export function fieldValidationSummary(
  fieldErrors: Record<string, readonly string[]>,
  fieldByName: ReadonlyMap<string, FieldDescriptor>,
  t: UiTranslate,
): string {
  const fields = Object.keys(fieldErrors).map(
    (name) => fieldByName.get(name)?.label || name,
  );
  return fields.length > 0
    ? t("form.fixHighlightedFieldsNamed", { fields: fields.join(", ") })
    : t("form.fixHighlightedFields");
}

function fieldErrorMessage(error: unknown): string {
  if (typeof error === "string") return error;
  if (
    error &&
    typeof error === "object" &&
    "message" in error &&
    (typeof error.message === "string" || typeof error.message === "number")
  ) {
    return String(error.message);
  }
  return String(error);
}

export function recordSubtitleParts(
  record: Row | null | undefined,
  id: string | null | undefined,
  fields: DataResourceSubtitleMetadata | null | undefined,
  t: UiTranslate,
): ReactNode[] {
  const parts: ReactNode[] = [];
  const recordId = presentValue(record?.id) ?? presentValue(id);
  if (recordId !== undefined) parts.push(recordIdLabel(String(recordId)));
  if (record) {
    const created = fieldValue(record, fields?.created);
    const updated = fieldValue(record, fields?.updated);
    const words = fieldValue(record, fields?.wordCount);
    if (created !== undefined) {
      parts.push(t("form.created", { value: formatRecordDate(created) }));
    }
    if (updated !== undefined) {
      parts.push(t("form.updated", { value: formatRecordDate(updated) }));
    }
    if (words !== undefined) parts.push(formatWordCount(words, t));
  }
  return parts.filter((part) => String(part).trim() !== "");
}

function fieldValue(
  record: Row,
  field: string | null | undefined,
): unknown | undefined {
  if (!field) return undefined;
  return presentValue(rowValueAtPath(record, field));
}

function presentValue(value: unknown): unknown | undefined {
  if (value == null) return undefined;
  if (typeof value === "string" && value.trim() === "") return undefined;
  return value;
}

function recordIdLabel(value: string): string {
  return publicIdLabel(value) ?? shortRecordId(value);
}

function shortRecordId(value: string): string {
  const text = value.trim();
  return text.length <= 12 ? text : text.slice(0, 8);
}

function formatRecordDate(value: unknown): string {
  const date = dateFromUnknown(value);
  return date ? formatDate(date) : String(value);
}

function formatWordCount(value: unknown, t: UiTranslate): string {
  const count =
    typeof value === "number"
      ? value
      : typeof value === "string"
        ? Number(value)
        : Number.NaN;
  if (Number.isFinite(count)) {
    return t("form.wordCount", { count: new Intl.NumberFormat().format(count) });
  }
  return t("form.wordCount", { count: String(value) });
}

function normaliseFieldName(value: string): string {
  return value.replace(/[-_\s]+/g, "").toLowerCase();
}

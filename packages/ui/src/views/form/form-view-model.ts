import type { ReactNode } from "react";
import { get, set } from "react-hook-form";
import { replaceEqualDeep } from "@tanstack/react-query";
import {
  publicIdLabel,
  relationModelLabelForField,
  rowPublicId,
  rowValueAtPath,
  type DataResourceSubtitleMetadata,
  type ModelFieldMetadata,
  type ModelMetadata,
  type QueryField,
  type Row,
} from "@angee/metadata";

import { formatNumber } from "../../lib/format-number";
import { dateFromUnknown, formatDate } from "../../widgets/date-format";
import { canonicalOptionValue, relationIdList, relationValueId } from "../../widgets/types";
import type { UiTranslate } from "../../i18n";
import {
  fieldWidgetId,
  isRelationIdField,
  isRelationListField,
  type FieldDescriptor,
  type GroupDescriptor,
} from "../page";
import type { RelationFieldInfo } from "../resource/model-metadata-defaults";
import { isStructuredPresenceField, structuredFieldErrorPaths, textValue } from "./field-values";

export type FormValues = Record<string, unknown>;
const MISSING_DOTTED_VALUE = Symbol("missing-dotted-value");

/** Preserve dirty array values atomically instead of merging server rows by index. */
export function dirtyArrayValues(
  values: FormValues,
  isDirty: (path: string) => boolean,
  prefix = "",
): readonly (readonly [string, unknown[]])[] {
  return Object.entries(values).flatMap(([name, value]) => {
    const path = prefix ? `${prefix}.${name}` : name;
    if (Array.isArray(value)) {
      return isDirty(path) ? [[path, structuredClone(value)] as const] : [];
    }
    return value && typeof value === "object"
      ? dirtyArrayValues(value as FormValues, isDirty, path)
      : [];
  });
}

/** Child lines threaded through a form reset alongside declared field values. */
export interface LinesSeed {
  field: string;
  rows: readonly Row[];
}

export interface FormSectionModel {
  key: string;
  label?: ReactNode;
  hint?: ReactNode;
  audience?: ReactNode;
  icon?: ReactNode;
  badge?: ReactNode;
  columns?: number;
  collapsible?: boolean;
  defaultOpen?: boolean;
  fields: readonly FieldDescriptor[];
  render?: () => ReactNode;
  /** Projected record permission required to show this section. */
  permission?: string;
  /** The container child behind it, shown only on records that admit that child. */
  containerChild?: string;
  /** The pane this section fills instead of the sheet. */
  pane?: string;
  sequence?: number;
  order?: number;
}

export interface FormViewFieldLayout {
  titleField: FieldDescriptor | undefined;
  titlePlacementField: FieldDescriptor | undefined;
  statusField: FieldDescriptor | undefined;
  bodyField: FieldDescriptor | undefined;
  gridFields: readonly FieldDescriptor[];
  gridGroups: readonly GroupDescriptor[];
}

/** The section key of a form's editable lines, declared by a `lines` group or trailing the form. */
export const EDITABLE_LINES_SECTION = "editable-lines";

export function formSections(
  fields: readonly FieldDescriptor[],
  groups: readonly GroupDescriptor[],
  sequences: readonly (number | undefined)[] = [],
  isCreate = false,
): readonly FormSectionModel[] {
  if (groups.length === 0) return [{ key: "fields", fields }];
  if (groups.filter((group) => group.lines).length > 1) {
    throw new Error("A form declares its editable lines in one group.");
  }
  if (groups.some((group) => group.lines && group.pane !== undefined)) {
    throw new Error("A form's editable lines group cannot also be a pane.");
  }
  const groupedNames = new Set<string>();
  const sections: FormSectionModel[] = groups.flatMap((group, index) => {
    // A saved-only group's fields stay out of a create form, never falling back to the ungrouped section.
    for (const field of group.fields) groupedNames.add(field.name);
    if (isCreate && group.savedOnly) return [];
    if (group.fields.length === 0 && group.content === undefined && !group.lines) return [];
    return [
      {
        key: group.lines ? EDITABLE_LINES_SECTION : `group:${index}:${String(group.label ?? "")}`,
        label: group.label,
        ...(group.hint !== undefined ? { hint: group.hint } : {}),
        ...(group.audience !== undefined ? { audience: group.audience } : {}),
        columns: group.columns,
        collapsible: group.collapsible,
        defaultOpen: group.defaultOpen,
        fields: group.fields,
        ...(group.content !== undefined ? { render: () => group.content } : {}),
        ...(group.permission !== undefined ? { permission: group.permission } : {}),
        ...(group.containerChild !== undefined ? { containerChild: group.containerChild } : {}),
        ...(group.pane !== undefined ? { pane: group.pane } : {}),
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
  isCreate = false,
): FormViewFieldLayout {
  // Explicit pane placement wins over automatic hero inference. Otherwise a
  // description or status in a secondary pane is pulled back above every tab.
  const paneFields = new Set(resolvedGroups.filter((group) => group.pane !== undefined)
    .flatMap((group) => group.fields.map((field) => field.name)));
  const sheetFields = formFields.filter((field) => !paneFields.has(field.name));
  const titleField = titleFieldFor(sheetFields, metadata);
  const titlePlacementField = isCreate ? undefined : sheetFields.find((field) => field.placement === "title");
  const statusField = sheetFields.find(
    (field) => fieldWidgetId(field) === "statusbar" || (field.status && !field.showWhen),
  );
  const bodyField = bodyFieldFor(sheetFields, titleField, statusField);
  const excluded = new Set(
    [titleField?.name, titlePlacementField?.name, statusField?.name, bodyField?.name].filter(
      (name): name is string => name !== undefined,
    ),
  );
  return {
    titleField,
    titlePlacementField,
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
  if (!record || !metadata) return undefined;
  const values = record as Record<string, unknown>;
  const representation = metadata.resource.recordRepresentation;
  const value = representation ? values[representation] : undefined;
  if (textValue(value)) return value;
  return values[metadata.resource.query.identity.field];
}

export function titleText(value: unknown, fallback: string): string {
  const text = textValue(value);
  return text || fallback;
}

export function addFieldSelection(
  paths: Set<string>,
  field: FieldDescriptor,
  relation?: RelationFieldInfo,
  metadata?: ModelFieldMetadata,
  queryField?: QueryField,
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
  if (metadata?.kind === "object") {
    const prefix = field.name.slice(0, -metadata.name.length);
    for (const path of queryField?.row?.paths ?? []) paths.add(`${prefix}${path}`);
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
  for (const field of visibleFields(fields, evaluation)) {
    const value = initialDraftFieldValue(field, defaultValues);
    if (value !== MISSING_DOTTED_VALUE) setDottedValue(draft, field.name, cloneFormValue(value));
  }
  return draft;
}

/** The fields a draft for `values` holds: those visible for them, resolved against them. */
export function visibleFields(fields: readonly FieldDescriptor[], values: FormValues): FieldDescriptor[] {
  return fields.map((field) => resolveField(field, values)).filter((field) => isFieldVisible(field, values));
}

/** How fields named `names` hold `path`, by segment: one is it or above it, some below it, or none. */
export function pathHolding(names: readonly string[], path: string): "whole" | "part" | null {
  if (names.some((name) => path === name || path.startsWith(`${name}.`))) return "whole";
  return names.some((name) => name.startsWith(`${path}.`)) ? "part" : null;
}

/**
 * Of the `targets` a preset set, the paths a draft for `values` does not hold (see `emptyDraft`):
 * a target a declared field relates to and no visible field holds, or, under a target whose own
 * visible fields remain, what they do not hold. Nothing outside the targets is touched.
 */
export function hiddenDraftPaths(
  values: FormValues,
  fields: readonly FieldDescriptor[],
  targets: readonly string[],
): string[] {
  const visible = visibleFields(fields, values).map((field) => field.name);
  const walk = (value: unknown, path: string): string[] => {
    const held = pathHolding(visible, path);
    if (held === "whole") return [];
    if (held === null || value === null || typeof value !== "object" || Array.isArray(value)) return [path];
    return Object.entries(value).flatMap(([key, child]) => walk(child, `${path}.${key}`));
  };
  const declared = fields.map((field) => field.name);
  return targets
    .filter((target) => pathHolding(declared, target) !== null)
    .flatMap((target) => walk(dottedValue(values, target), target));
}

/**
 * What one form remembers so a dirty-preserving preset keeps the person's own values (see
 * `seedPreset`): the paths the person changed themselves, and the seed last written to each target
 * without dirtying the form. The form's owner creates one per form and it lasts as long as the
 * person's editing does: a record form clears it on discard, a dialog's ends with its provider.
 */
export interface PresetMemory {
  /** The person changed the value at `path` themselves. */
  noteEdit(path: string): void;
  /** A preset wrote `seed` at `path` without dirtying the form. */
  noteSeed(path: string, seed: unknown): void;
  /**
   * Whether the value at `path` is the person's own: they changed it themselves, or it is `dirty`
   * and no longer, by deep equality, the seed last written there.
   */
  isOwn(path: string, state: { dirty: boolean; value: unknown }): boolean;
  /** Forget every edit and seed. */
  clear(): void;
}

/** A form's empty `PresetMemory`. */
export function createPresetMemory(): PresetMemory {
  const edited = new Set<string>();
  const seeds = new Map<string, unknown>();
  return {
    noteEdit: (path) => { edited.add(path); },
    noteSeed: (path, seed) => { seeds.set(path, seed); },
    isOwn: (path, { dirty, value }) => edited.has(path)
      || (dirty && replaceEqualDeep(seeds.get(path), value) !== seeds.get(path)),
    clear: () => { edited.clear(); seeds.clear(); },
  };
}

/** Whether `preset` leaves the person's own value at `target`: it preserves dirty values and does not name `target` to replace. */
function preservesTarget(preset: FieldDescriptor, target: string): boolean {
  return Boolean(preset.prefillPreserveDirty) && !preset.prefillReplace?.includes(target);
}

/**
 * Write what `preset` seeds when its field changes to `value` (see `FieldDescriptor.prefill`), from
 * the `values` and `loaded` values of its scope, and return the form paths written, in the preset's
 * order. A `createOnly` preset writes nothing outside a create. A `prefillPreserveDirty` preset skips
 * a target whose value `memory` says is the person's own, unless `prefillReplace` names it, and writes
 * its other unnamed targets without dirtying the form, noting each such seed in `memory`; every other
 * seed dirties the form. A target is `read` only on its turn, after the seeds before it were written.
 */
export function seedPreset(
  preset: FieldDescriptor,
  value: unknown,
  values: FormValues,
  loaded: FormValues | undefined,
  options: {
    isCreate: boolean;
    memory: PresetMemory;
    /** The form path of a target the preset names: its scope in a nested form. */
    at?: (target: string) => string;
    /** The form's dirty flag and value at a path, as they are when asked. */
    read: (path: string) => { dirty: boolean; value: unknown };
    /** The caller's own write of a seed; `dirties` says whether it dirties the form. */
    write: (path: string, seed: unknown, dirties: boolean) => void;
  },
): string[] {
  if (preset.createOnly && !options.isCreate) return [];
  const written: string[] = [];
  for (const [target, seed] of Object.entries(preset.prefill?.(value, values, loaded) ?? {})) {
    const path = options.at?.(target) ?? target;
    const preserves = preservesTarget(preset, target);
    if (preserves && options.memory.isOwn(path, options.read(path))) continue;
    options.write(path, seed, !preserves);
    if (preserves) options.memory.noteSeed(path, seed);
    written.push(path);
  }
  return written;
}

/**
 * The loaded values a draft lacks: each field visible for `values` whose key the draft does not
 * own (a dropped field), where `baseline` holds a value. A field left unset owns its key.
 */
export function missingDraftValues(
  values: FormValues,
  fields: readonly FieldDescriptor[],
  baseline: FormValues,
): ReadonlyMap<string, unknown> {
  const owns = (path: string) => {
    let node: unknown = values;
    for (const segment of path.split(".")) {
      if (node === null || typeof node !== "object" || !Object.hasOwn(node, segment)) return false;
      node = (node as FormValues)[segment];
    }
    return true;
  };
  return new Map(visibleFields(fields, values)
    .filter((field) => !owns(field.name) && hasDottedValue(baseline, field.name))
    .map((field) => [field.name, dottedValue(baseline, field.name)]));
}

/**
 * What a save writes for hidden fields (see `FieldDescriptor.prefill`), derived from the values
 * alone: each visible field the save writes whose value changed has its preset evaluated again,
 * and each seed it replaces is written whose path a field the save writes owns, no visible field
 * holds, and the form was loaded with a different value for.
 */
export function hiddenResetWrites(
  values: FormValues,
  fields: readonly FieldDescriptor[],
  options: FieldSaveOptions & { baseline: FormValues },
): ReadonlyMap<string, unknown> {
  const writes = new Map<string, unknown>();
  if (options.isCreate) return writes;
  const visible = visibleFields(fields, values);
  const visibleNames = visible.map((field) => field.name);
  const same = (left: unknown, right: unknown) => replaceEqualDeep(left, right) === left;
  for (const field of visible) {
    if (!field.prefill || field.createOnly || !savesField(field, options)) continue;
    const value = dottedValue(values, field.name);
    if (same(value, dottedValue(options.baseline, field.name))) continue;
    for (const [path, seed] of Object.entries(field.prefill(value, values, options.baseline) ?? {})) {
      // A seed of nothing says nothing; a dirty-preserving preset's other fields are defaults.
      if (seed === undefined || preservesTarget(field, path)) continue;
      const owner = fields.find((candidate) => candidate.name === path);
      if (!owner || !savesField(resolveField(owner, values), options) || pathHolding(visibleNames, path) !== null) continue;
      if (hasDottedValue(options.baseline, path) && !same(seed, dottedValue(options.baseline, path))) {
        writes.set(path, seed);
      }
    }
  }
  return writes;
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
  for (const field of visibleFields(fields, record)) {
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

/** A control is rendered only when it is neither hidden nor conditionally excluded. */
export function isFieldControlVisible(field: FieldDescriptor, values: FormValues): boolean {
  return !field.hidden && isFieldVisible(field, values);
}

export function missingRequiredFieldNames(
  values: FormValues,
  fields: readonly FieldDescriptor[],
  requiredFieldNames: ReadonlySet<string>,
): readonly string[] {
  return visibleFields(fields, values).flatMap((field) => {
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
  return sections.map((section) => ({ ...section, fields: visibleFields(section.fields, values) }));
}

/** What decides whether a save writes a field at all. */
interface FieldSaveOptions {
  isCreate: boolean;
  seededFieldNames?: ReadonlySet<string> | null;
  writableFields?: ReadonlySet<string> | null;
}

/** A create seed, written on create even when the field is read-only. */
function createSeeded(field: FieldDescriptor, options: FieldSaveOptions): boolean {
  return options.isCreate && !field.editOnly
    && (field.defaultValue !== undefined || (options.seededFieldNames?.has(field.name) ?? false));
}

/** Whether a save may write `field`: one the record lets it write, editable or a create seed. */
function savesField(field: FieldDescriptor, options: FieldSaveOptions): boolean {
  return (!options.writableFields || isWritableDottedField(options.writableFields, field.name))
    && (!field.readOnly || createSeeded(field, options));
}

export function mutationData(
  values: FormValues,
  fields: readonly FieldDescriptor[],
  options: FieldSaveOptions & {
    dirtyFields: Record<string, unknown>;
    id?: string | null;
    fieldMetadata?: Readonly<Record<string, ModelFieldMetadata>> | null;
    /** The resets `hiddenResetWrites` decided this save writes for the fields a preset hid. */
    hiddenResets?: ReadonlyMap<string, unknown> | null;
  },
): FormValues {
  const data: FormValues = {};
  for (const declared of fields) {
    const field = resolveField(declared, values);
    if (!isFieldVisible(field, values)) {
      // A preset's value for a hidden field goes on the wire as the field's own value would.
      if (options.hiddenResets?.has(field.name)) {
        setDottedValue(data, field.name, mutationFieldValue(field, cloneFormValue(options.hiddenResets.get(field.name))));
      }
      continue;
    }
    if (!savesField(field, options)) continue;
    const seededDefault = createSeeded(field, options);
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

/**
 * `field` as it presents for `values`: what its `resolve` returns over its declaration, read-only
 * when either is. A `resolve` never renames the value: the field keeps reading and writing its
 * declared `name`, whatever name the resolved descriptor carries.
 */
export function resolveField(field: FieldDescriptor, values: FormValues): FieldDescriptor {
  const resolved = field.resolve?.(values);
  return resolved
    ? { ...field, ...resolved, name: field.name, readOnly: Boolean(field.readOnly || resolved.readOnly) }
    : field;
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

/**
 * The form value of a to-many relation after its picker reports `next`. A set
 * equal to the baseline's takes the baseline's value, so picking the same
 * records again, in any order, leaves the field unchanged; otherwise retained
 * ids keep their loaded records and labels. The wire value is the id list.
 */
export function relationListValue(next: unknown, baseline: unknown): unknown[] {
  const ids = relationIdList(next);
  const saved = Array.isArray(baseline) ? baseline : [];
  const savedIds = relationIdList(saved);
  if (ids.length === savedIds.length && ids.every((id) => savedIds.includes(id))) return [...saved];
  const records = new Map(saved.map((record) => [relationValueId(record), record]));
  return ids.map((id) => records.get(id) ?? id);
}

function mutationFieldValue(field: FieldDescriptor, value: unknown): unknown {
  if (isRelationIdField(field)) return relationValueId(value);
  if (isRelationListField(field)) return relationIdList(value);
  // NumberField keeps an ordinary keyboard clear as an editable blank string
  // so the user can type a replacement without leaving value mode. The wire
  // boundary owns the nullable scalar representation.
  if (isNumericField(field) && value === "") return null;
  return value;
}

function emptyValue(field: FieldDescriptor): unknown {
  if (isNumericField(field)) return null;
  if (isNullableScalarWidget(field)) return null;
  if (field.widget === "tagInput" || isRelationListField(field)) return [];
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
  return id === "date" || id === "datetime" || id === "time";
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
    return metadata.kind === "enum" || !isStringScalar(metadata) || (
      metadata.requiredOnCreate === false
      && !intent.dirty && !intent.seeded && !field.presenceRequired
    );
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

/** Whether a descriptor owns structured child controls through a template.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function isCompositeFieldDescriptor(field: FieldDescriptor): boolean {
  return Boolean(field.objectTemplate || field.itemTemplate || ("rowTemplate" in field && field.rowTemplate));
}

/**
 * Exact-field messages stay bare. With a root path, descendants keep their full
 * dotted paths for nested routing; an empty root produces relative paths.
 * Omit the root to leave every message unprefixed.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function fieldErrorMessages(errors: readonly unknown[], path?: string): string[] {
  return errors.flatMap((error) => nestedFieldErrorMessages(error, path, path));
}

function nestedFieldErrorMessages(error: unknown, path?: string, rootPath?: string): string[] {
  if (error == null) return [];
  if (typeof error !== "object") return [String(error)];
  const messages: string[] = [];
  const hasMessage = "message" in error
    && (typeof error.message === "string" || typeof error.message === "number");
  if (hasMessage) {
    const message = String(error.message);
    messages.push(path && path !== rootPath ? `${path}: ${message}` : message);
  }
  return messages.concat(Object.entries(error).flatMap(([name, child]) => {
    if (name === "ref" || name === "type" || child === undefined
      || (hasMessage && (name === "message" || name === "types"))) return [];
    const childPath = path === undefined ? undefined : path ? `${path}.${name}` : name;
    return nestedFieldErrorMessages(child, childPath, rootPath);
  }));
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

export function recordSubtitleParts(
  record: Row | null | undefined,
  id: string | null | undefined,
  fields: DataResourceSubtitleMetadata | null | undefined,
  t: UiTranslate,
  developerMode = false,
): ReactNode[] {
  const parts: ReactNode[] = [];
  const recordId = presentValue(record?.id) ?? presentValue(id);
  if (developerMode && recordId !== undefined) parts.push(recordIdLabel(String(recordId)));
  if (record) {
    const created = fieldValue(record, fields?.created);
    const updated = fieldValue(record, fields?.updated);
    const words = fieldValue(record, fields?.wordCount);
    if (developerMode && created !== undefined) {
      parts.push(t("form.created", { value: formatRecordDate(created) }));
    }
    if (developerMode && updated !== undefined) {
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
    return t("form.wordCount", { count: formatNumber(count) });
  }
  return t("form.wordCount", { count: String(value) });
}

function normaliseFieldName(value: string): string {
  return value.replace(/[-_\s]+/g, "").toLowerCase();
}

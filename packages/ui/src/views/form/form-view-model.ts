import type { ReactNode } from "react";
import { get, set, type FieldErrors } from "react-hook-form";
import { replaceEqualDeep } from "@tanstack/react-query";
import {
  holdsPermission,
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
import { DATE_TIME_WIDGETS, NUMBER_WIDGETS, emptyValueForField, fieldRuleErrors, refusesSeed, textValue } from "./field-values";
import { fieldErrorsFromRules } from "./validation-errors";

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

/**
 * Whether a record's form displays a declared group: the one place its conditions are decided. A
 * `savedOnly` group is no part of a create form; a group carrying a `permission` shows on a record
 * that holds it (`holdsPermission`), so never without a record; a group a row-decided container
 * child contributed (`containerChild`) shows where `admits`, the form's container admission, admits
 * that child for the record. The sheet, its panes and the fields the form checks and saves
 * (`displayedFormFields`) all read it: a field only groups it does not display declare is not that
 * record's form field. A contribution's tabs ask it for their child's permission and admission, its
 * actions for their child's admission alone: a contribution's permission does not gate the actions
 * it contributes.
 */
export function formDisplaysGroup(
  group: Pick<GroupDescriptor, "savedOnly" | "permission" | "containerChild">,
  record: Row | null,
  { isCreate, admits }: { isCreate: boolean; admits: (child: string, record: Row | null) => boolean },
): boolean {
  return !(isCreate && group.savedOnly)
    && (group.permission === undefined || holdsPermission(record, group.permission))
    && (group.containerChild === undefined || admits(group.containerChild, record));
}

/** The names of the fields `groups` declare: none of them is one of a form's ungrouped fields. */
function groupFieldNames(groups: readonly GroupDescriptor[]): ReadonlySet<string> {
  return new Set(groups.flatMap((group) => group.fields.map((field) => field.name)));
}

/**
 * A form's sections: its ungrouped fields, then one per declared group it `displays` (see
 * `formDisplaysGroup`), keyed by the group's index among all `groups`, so a group left out
 * renumbers no other. The first `pageGroupCount` of `groups` are the page's own, the rest
 * contributed. The ungrouped section holds the `fields` that neither one of the page's own groups,
 * displayed or not, nor a displayed contributed group declares.
 */
export function formSections(
  fields: readonly FieldDescriptor[],
  groups: readonly GroupDescriptor[],
  sequences: readonly (number | undefined)[] = [],
  displays: (group: GroupDescriptor) => boolean = () => true,
  pageGroupCount = groups.length,
): readonly FormSectionModel[] {
  if (groups.length === 0) return [{ key: "fields", fields }];
  if (groups.filter((group) => group.lines).length > 1) {
    throw new Error("A form declares its editable lines in one group.");
  }
  if (groups.some((group) => group.lines && group.pane !== undefined)) {
    throw new Error("A form's editable lines group cannot also be a pane.");
  }
  const sections: FormSectionModel[] = groups.flatMap((group, index) => {
    if (!displays(group)) return [];
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
        ...(group.pane !== undefined ? { pane: group.pane } : {}),
        sequence: sequences[index],
        order: index,
      },
    ];
  });
  // A field of the page's own undisplayed group stays out of the form, never falling back to the
  // ungrouped section; a page's own field only an undisplayed contribution re-declares shows there.
  const grouped = groupFieldNames(groups.filter((group, index) => index < pageGroupCount || displays(group)));
  const ungrouped = fields.filter((field) => !grouped.has(field.name));
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

/**
 * One record's form fields: `flattenedFormFields` over the declarations its form displays, the
 * first declaration of a name winning. They are the form's field list `fields`, less the fields of
 * the page's own groups that `displays` (see `formDisplaysGroup`) rejects, which a page's field list
 * also lists; then the fields of the `groups` it displays, in their order. The first
 * `pageGroupCount` of `groups` are the page's own, the rest contributed. A field the page declares
 * outside its own groups keeps the page's declaration whatever a contribution re-declares, and with
 * every group displayed this is `flattenedFormFields(fields, groups)`, declaration for declaration.
 */
export function displayedFormFields(
  fields: readonly FieldDescriptor[],
  groups: readonly GroupDescriptor[],
  displays: (group: GroupDescriptor) => boolean,
  pageGroupCount: number,
): readonly FieldDescriptor[] {
  const undisplayed = groupFieldNames(groups.filter((group, index) => index < pageGroupCount && !displays(group)));
  return flattenedFormFields(fields.filter((field) => !undisplayed.has(field.name)), groups.filter(displays));
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
export function visibleFields<T extends FieldDescriptor>(fields: readonly T[], values: FormValues): T[] {
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

/**
 * A create draft's seed for `field`: the page's default value, else the field's own, else nothing
 * for an omittable or presence-required field, else its empty value (`emptyValueForField`), which a
 * field that may be left out does not take where its declared type refuses it (`refusesSeed`).
 */
function initialDraftFieldValue(
  field: FieldDescriptor,
  defaultValues: Record<string, unknown> | undefined,
): unknown {
  if (hasDottedValue(defaultValues, field.name)) return dottedValue(defaultValues, field.name);
  if (field.hasDefault || field.defaultValue !== undefined) return field.defaultValue;
  if (field.omittable || field.presenceRequired) return MISSING_DOTTED_VALUE;
  const seed = emptyValueForField(field);
  return refusesSeed(field, seed) ? MISSING_DOTTED_VALUE : seed;
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
    setDottedValue(values, field.name, value === undefined ? emptyValueForField(field) : value);
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

/**
 * A record form's errors for the rules its `fields` fail (the fields its save can write,
 * `pathWritable`, and the record does not redact, `fieldIsRedacted`, of those the form displays
 * for the record, `displayedFormFields`, and the rail rows `displayedRailGroups` holds), each
 * field's found by `fieldRuleErrors` and worded by `fieldErrorsFromRules`. As a dialog's check
 * (`dialogRuleErrors`) does, it asks only the fields whose descriptor is shown for `values`
 * (`isFieldControlVisible`: neither hidden nor conditionally excluded) and not read-only, and
 * leaves the others to the server; a field only a group the record does not display declares, one
 * the save cannot write and one the record redacts are not among `fields`, so they are not asked,
 * whatever their widget.
 * It differs from a dialog's check in four facts only: it reads read-only from the field as
 * resolved for `values` (its own flag, its `resolve`, its mode on a create or an edit), where a
 * dialog also reads `readOnlyWhen`; it reads each value at its dotted path, where a dialog reads
 * one flat key (both read an own key holding `undefined` as absent); it counts a field as required
 * by its own flag or by `requiredFieldNames`, the model's required-on-create names, where a dialog
 * counts the flag alone; and it always checks constraints, which a dialog may leave to a schema
 * resolver.
 */
export function formRuleErrors(
  values: FormValues,
  fields: readonly FieldDescriptor[],
  requiredFieldNames: ReadonlySet<string>,
  t: UiTranslate,
): FieldErrors<FormValues> {
  return fieldErrorsFromRules(visibleFields(fields, values)
    .filter((field) => isFieldControlVisible(field, values) && !field.readOnly)
    .flatMap((field) => fieldRuleErrors(
      field,
      { value: dottedValue(values, field.name), present: hasDottedValue(values, field.name) },
      { required: Boolean(field.required) || requiredFieldNames.has(field.name), validateConstraints: true },
    )), t);
}

/**
 * `sections`, each with the fields it shows for `values`: those visible for them, resolved against
 * them (`visibleFields`), that `displays` keeps (a record form's `displaysField`). A section keeps
 * its key and place whatever fields it is left with (`isEmptySection` says when it has none).
 */
export function visibleSections(
  sections: readonly FormSectionModel[],
  values: FormValues,
  displays: (field: FieldDescriptor) => boolean = () => true,
): readonly FormSectionModel[] {
  return sections.map((section) => ({ ...section, fields: visibleFields(section.fields, values).filter(displays) }));
}

/**
 * Whether a section has nothing to show: no field and no content of its own. The sheet renders
 * nothing for it, and the pane strip lists no pane for it.
 */
export function isEmptySection(section: Pick<FormSectionModel, "fields" | "render">): boolean {
  return section.fields.length === 0 && section.render === undefined;
}

/** What decides whether a save writes a field at all. */
interface FieldSaveOptions {
  isCreate: boolean;
  seededFieldNames?: ReadonlySet<string> | null;
  /** The paths the save can write (`formWritablePaths`, read through `pathWritable`); none, every path. */
  writableFields?: ReadonlySet<string> | null;
}

/** A create seed, written on create even when the field is read-only. */
function createSeeded(field: FieldDescriptor, options: FieldSaveOptions): boolean {
  return options.isCreate && !field.editOnly
    && (field.defaultValue !== undefined || (options.seededFieldNames?.has(field.name) ?? false));
}

/** Whether a save may write `field`: one the record lets it write, editable or a create seed. */
function savesField(field: FieldDescriptor, options: FieldSaveOptions): boolean {
  return pathWritable(field.name, options.writableFields) && (!field.readOnly || createSeeded(field, options));
}

/**
 * Whether a record form's save can write the field at `path`: the one place a record form decides a
 * field's writability. What the save sends reads it, its resets of hidden fields included
 * (`hiddenResetWrites`), and so does which declared preset applies when a field changes; a preset
 * still writes any path into the form's values, and a field it fills is then displayed
 * (`formDisplaysField`). `writable` (`formWritablePaths`) names the path or its root; absent, under
 * the form's own submit owner, every path is writable. A path no record form field declares is
 * therefore not writable, unless the form's own submit owner saves it. Whether the record redacts
 * the path is another fact (`fieldIsRedacted`): a redacted field the form itself wrote (a preset's
 * write, a hidden reset) is sent, and one it did not write is not, since an edit's save sends only
 * what the form changed and a create has no record to redact anything. Limit: that holds for a flat
 * field. A structured field goes whole once one of its members changed, so where the record redacts
 * it and a preset writes one member, its untouched members go as the form loaded them, the record's
 * placeholders included.
 */
export function pathWritable(path: string, writable: ReadonlySet<string> | null | undefined): boolean {
  return !writable || writable.has(path) || writable.has(path.split(".", 1)[0] ?? path);
}

/**
 * Whether `record` redacts the field at `path`: its root is in the record's `redacted_fields`, what
 * the viewer may not read. The one place a record form decides a field's readability: the form
 * shows the field's lock and no control (`fieldControlReadOnly` makes any control of it
 * read-only), its check does not ask it, and its banner calls a refusal of it by its name rather
 * than highlighted.
 */
export function fieldIsRedacted(record: FormValues | null | undefined, path: string): boolean {
  const redacted = record?.redacted_fields;
  return Array.isArray(redacted) && redacted.includes(path.split(".", 1)[0]);
}

/**
 * The paths a record form's save writes, each a field's name or its root's (read through
 * `pathWritable`): of one record's form `fields`, the names `modeWritable` names, a field or its
 * root (the model's create fields on a create, its updatable fields on an edit; absent, every one
 * of them); or none, meaning every path, under the form's own submit owner (`ownSubmit`), which
 * receives the whole values and so may write any path.
 */
export function formWritablePaths(
  fields: readonly FieldDescriptor[],
  modeWritable: ReadonlySet<string> | null,
  ownSubmit: boolean,
): ReadonlySet<string> | null {
  return ownSubmit ? null : new Set(fields.map((field) => field.name).filter((name) => pathWritable(name, modeWritable)));
}

/**
 * Whether a record form shows the control of `field` read-only, the form's lock and a preview
 * aside: the field is read-only as declared and resolved; or the record redacts it (`redacted`,
 * `fieldIsRedacted`), whatever its widget declares; or its save cannot write it (`writable`,
 * `pathWritable`), unless on a saved record its widget writes its own value
 * (`WidgetDefinition.writesOwnValue`) and so decides its own editability. On a create that flag
 * changes nothing.
 */
export function fieldControlReadOnly(
  field: FieldDescriptor,
  { writable, redacted, isCreate, writesOwnValue }: { writable: boolean; redacted: boolean; isCreate: boolean; writesOwnValue: boolean },
): boolean {
  return Boolean(field.readOnly) || redacted || (!writable && (isCreate || !writesOwnValue));
}

/**
 * The record form's decision whether it displays a field, as `formDisplaysGroup` is its decision on a
 * group; on a create that decision reads the `value` the field holds. Whether a field is visible (its
 * `showWhen`, its `hidden`; `isFieldControlVisible`) is another fact, which this does not decide.
 * On a create, a field that holds nothing to display (`holdsNothingToDisplay`, an empty object
 * included) is not displayed where its save cannot write it (`writable`, `pathWritable`), nor where it
 * is the form's status and its control is read-only (`readOnlyStatus`): a status nobody sets on a
 * create holds nothing to display until the server gives it one. A field that holds a value (a seed,
 * a default, a preset's write) is displayed, read-only where it is, and an edit displays every
 * field. The sheet's sections, its panes and the form's status and body read it; a control a page
 * renders itself is the page's to display.
 */
export function formDisplaysField(
  value: unknown,
  { writable, isCreate, readOnlyStatus = false }: { writable: boolean; isCreate: boolean; readOnlyStatus?: boolean },
): boolean {
  return !isCreate || !holdsNothingToDisplay(value) || (writable && !readOnlyStatus);
}

/**
 * Whether `value` gives a record form nothing to display: `null` or `undefined`, a text of blanks
 * only, an empty list, an empty object. It is the display's own fact: what the rule check counts as
 * missing (`isEmptyFieldValue`, for which an empty object is a value) and what a create leaves out of
 * its payload (`isBlankCreateValue`) are others.
 */
export function holdsNothingToDisplay(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return typeof value === "object" && Object.getPrototypeOf(value) === Object.prototype && Object.keys(value).length === 0;
}

/**
 * What a record form's save sends for `values`, built from the `fields` it writes. It writes nothing
 * into `values`: what it takes from them it copies, so turning a structured root's dotted members into
 * their wire values changes what it returns, never the values it was given.
 */
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
    // A copy, as a hidden reset's: a structured root's dotted members may be written into what is
    // returned, and a write there must never reach `values`.
    setDottedValue(data, field.name, cloneFormValue(next));
  }
  if (!options.isCreate && options.id != null) data.id = options.id;
  return data;
}

/**
 * `field` as it presents for `values`, as the caller's descriptor type: what its `resolve` returns
 * over its declaration, read-only when either is, so a subtype's own members are kept from the
 * declaration or laid over by `resolve`. A `resolve` never renames the value: the field keeps
 * reading and writing its declared `name`, whatever name arrives.
 */
export function resolveField<T extends FieldDescriptor>(field: T, values: FormValues): T {
  const resolved = field.resolve?.(values);
  return resolved
    ? { ...field, ...resolved, name: field.name, readOnly: Boolean(field.readOnly || resolved.readOnly) }
    : field;
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

/**
 * A record form's copy of a value it reads: a deep copy (`structuredClone`) of an object or a list, that
 * shares nothing with `value`; anything else as it is. The save's payload and `nextSave` read through it.
 * A record form's values are plain data: `structuredClone` copies a class instance as a plain object
 * without its methods (a `toJSON` among them), and a function throws, as in the form's undo history
 * (`useFormHistory`), which already copies every value this way.
 */
export function cloneFormValue<T>(value: T): T {
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

function isNullableScalarWidget(field: FieldDescriptor): boolean {
  return DATE_TIME_WIDGETS.has(fieldWidgetId(field));
}

function isNumericField(field: FieldDescriptor): boolean {
  return NUMBER_WIDGETS.has(fieldWidgetId(field));
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
 * Omit the root to leave every message unprefixed. A bare value is a message only
 * where React Hook Form puts message text: as an error given here, or inside a
 * `types` subtree; anywhere else a child that is not an object is skipped. Under
 * `type`, any value that is not an object is the library's own attribute (or
 * `MEMBER_ERRORS`) and is skipped while an object is the error of a member named
 * `type` and is walked as any member's, and `ref`, where the library keeps the
 * registered control, is never walked, so a member named `ref` shows no message.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function fieldErrorMessages(errors: readonly unknown[], path?: string): string[] {
  return errors.flatMap((error) => nestedFieldErrorMessages(error, path, path));
}

/** `inTypes`: the error sits inside a `types` subtree, where a bare value is message text. */
function nestedFieldErrorMessages(error: unknown, path?: string, rootPath?: string, inTypes = false): string[] {
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
    const childInTypes = inTypes || name === "types";
    if (name === "ref" || child === undefined || (name === "type" && typeof child !== "object")
      || (typeof child !== "object" && !childInTypes)
      || (hasMessage && (name === "message" || name === "types"))) return [];
    const childPath = path === undefined ? undefined : path ? `${path}.${name}` : name;
    return nestedFieldErrorMessages(child, childPath, rootPath, childInTypes);
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

import * as React from "react";
import {
  fieldUpdatable,
  holdsPermission,
  useCanonicalResourceModelLabels,
  useResourceInvalidates,
  refineResourceName,
  type DataResourceLinesMetadata,
  type DataResourceMetadata,
  type ModelMetadata,
  type Row,
} from "@angee/metadata";
import { invalidateAuthoredQueries, useAngeeResourceSave, type MutationRootArguments } from "@angee/refine";
import {
  useInvalidate,
  useOne,
  useCreate,
  useUpdate,
  useKeys,
  useResourceParams,
  type GetOneResponse,
  type BaseKey,
  type BaseRecord,
  type Fields,
  type HttpError,
} from "@refinedev/core";
import { get, set, useForm } from "react-hook-form";
import { replaceEqualDeep, useMutation, useQueryClient } from "@tanstack/react-query";
import { deepEqual } from "@tanstack/react-router";

import type { UiTranslate } from "../../i18n";
import { useToast } from "../../feedback";
import { useDraftScope, useIdempotencyDraftOwner, useIdempotencyKeys } from "../../lib/idempotency-key";
import { asWidgetDefinition, slugify } from "../../widgets";
import { fieldWidgetId, type FieldDescriptor } from "../page";
import {
  diffLines,
  lineDiffConfig,
  reconcileAcceptedLineRows,
  recordLinesToRows,
  sameObservedLines,
  type LineDiff,
} from "./editable-lines";
import {
  baselineLineRows,
  cloneFormValue,
  createPresetMemory,
  dirtyArrayValues,
  emptyDraft,
  fieldControlReadOnly,
  fieldIsRedacted,
  fieldValidationSummary,
  formDisplaysField,
  formRuleErrors,
  formWritablePaths,
  hiddenDraftPaths,
  hiddenResetWrites,
  missingDraftValues,
  mutationData,
  pathWritable,
  recordToValues,
  resolveField,
  seedPreset,
  shownFields,
  visibleFields,
  type FormValues,
  type LinesSeed,
} from "./form-view-model";
import { useSaveOperation } from "../resource/resource-operations";
import {
  applyFormErrors, formErrorsFromForm, formSubmitError, formSubmitOutcome, markMemberErrors, savedFormSubmitResult, serverErrorsFromForm, useRefusalFocus, type FormSubmitResult,
} from "./validation-errors";
import { useUnsavedChangesNavigationGuard, type UnsavedChangesMove } from "./use-unsaved-changes-navigation-guard";
import { useFormHistory, type FormHistory } from "./use-form-history";
import { useFieldValidation, type FieldValidationForm, type FieldValidationMethods } from "./use-field-validation";
import { useAppRuntime, useRuntimeViewAs } from "../../runtime";
import { RECORD_TAB_SEARCH_KEY } from "../../runtime/route-href";
import { useLatestRef } from "../../lib/use-latest-ref";

type RowRecord = BaseRecord & Row;

export interface FormSubmitContext {
  resource: string;
  id: string | null;
  isCreate: boolean;
  record: Row | null;
  /** Saved record at the start of this local edit, held while the form is dirty. */
  baselineRecord: Row | null;
  /** One create attempt, retained across failures and renewed after acceptance/conflict. */
  idempotencyKey: string | null;
  lines: LineDiff | null;
  /** Exact RHF snapshot submitted by the user, including externally supplied fields. */
  values: FormValues;
  /** Last acknowledged snapshot against which this submission became dirty. */
  baselineValues: FormValues;
}

export interface FormSubmitAcknowledgement {
  kind: "form-submit-acknowledgement";
  record: Row;
  /** Complete server-accepted form snapshot, including generated identities. */
  values: FormValues;
  /** Optional domain-owned correlation when RHF's atomic dirty branches are insufficient. */
  reconcile?: (snapshots: {
    accepted: FormValues;
    submitted: FormValues;
    current: FormValues;
  }) => FormValues;
}

/**
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function acknowledgeFormSubmit(
  record: Row,
  values: FormValues,
  reconcile?: FormSubmitAcknowledgement["reconcile"],
): FormSubmitResult<FormSubmitAcknowledgement> {
  return { status: "ok", data: { kind: "form-submit-acknowledgement", record, values, ...(reconcile ? { reconcile } : {}) } };
}

export type FormSubmit = (
  data: Record<string, unknown>,
  context: FormSubmitContext,
) => FormSubmitResult<Row | FormSubmitAcknowledgement> | Promise<FormSubmitResult<Row | FormSubmitAcknowledgement>>;

export interface FormViewAcknowledgedSource {
  /** Record facts used by shared record chrome and addon declarations. */
  record: Row | null;
  /** Complete acknowledged form baseline. Local dirty values survive later snapshots. */
  values: FormValues | null;
  loading?: boolean;
  reload?: () => void | Row | null | Promise<void | Row | null>;
}

export type FormViewForm = FieldValidationForm<FormValues>;

/**
 * What a record form's Save would hand its writer for the values the form holds now (`nextSave`). The
 * native update sends `data` as its input. The native create sends `data`, with the lines' `payload`
 * under the lines field as a nested insert when the lines changed. An edit whose lines changed goes
 * through the resource save, `data` as its patch and the `payload` as its lines. The form's own submit
 * owner receives `data` and its context's `lines`, and may map them further.
 */
export interface FormNextSave {
  /**
   * The record's input, of the fields the save can write (`pathWritable`) whose condition holds for the
   * values (a `hidden` field, which has no control, included), with the values it writes for the fields
   * a preset hid. On an edit, the fields that changed: a structured field the form saves as a whole goes
   * whole, with its unchanged members, and where the form saves only some of its dotted members, only
   * those go. On a create, the create input less what it leaves out as blank. On an edit where this holds
   * no key and `lines` holds no change, Save still sends that empty update; what a server does with it
   * is not the form's to know.
   */
  data: FormValues;
  /** The lines diff of a form that edits its lines (`diffLines`, its `payload` the whole list); `null` where the form edits none. */
  lines: LineDiff | null;
}

export interface UseFormViewSaveProps {
  resource: string;
  id?: string | null;
  isCreate: boolean;
  readOnly?: boolean;
  dataResource: DataResourceMetadata | null;
  modelMetadata: ModelMetadata | null;
  formFields: readonly FieldDescriptor[];
  /**
   * One record's form fields, when they depend on the record: its own, and
   * those of the groups it displays and of its rail rows (a `savedOnly` group
   * only on a saved record, a section or a rail row only with its permissions,
   * a rail row only where the metadata reads its field, a container child's
   * only where the record admits it). The save writes those it can
   * (`pathWritable`); `formFields` holds every candidate.
   */
  formFieldsFor?: (record: Row | null) => readonly FieldDescriptor[];
  fieldByName: ReadonlyMap<string, FieldDescriptor>;
  refineFields: Fields;
  defaultValues?: Record<string, unknown>;
  acknowledgedSource?: FormViewAcknowledgedSource;
  onSaved?: (row: Row) => void;
  submit?: FormSubmit;
  createSubmit?: FormSubmit;
  /** Additional model caches refreshed after an accepted save. */
  invalidateModels?: readonly string[];
  /** Lock editing from persisted state; lifecycle actions remain independent. */
  readOnlyWhen?: (record: Row) => boolean;
  defaultSlugSource?: string;
  onFieldInteractionStart?: (path: string) => void;
  onFieldInteractionCommit?: (path: string) => void;
  onDiscarded?: () => void;
  /**
   * Bring a refused field into sight: a refused save or record patch that brings the person to a
   * field (`applyFormErrors`'s `focus` names one) calls it with that field, then asks the refusal's
   * focus, in the same batch; one that brings them to the banner does not call it. A save the
   * form's own check stops calls it with the first errored field, which the form library then
   * focuses. The record form passes its surface's `revealField` through a ref, because this hook
   * runs before the surface knows its panes.
   */
  revealField?: (path: string) => void;
  t: UiTranslate;
}

export interface FormViewSaveSurface {
  form: FormViewForm;
  history: FormHistory;
  /**
   * The form's draft scope (`useDraftScope`): the new record it creates, or the record it edits,
   * under the scope of the surface hosting it. The form names it to the drafts below it.
   */
  draftScope: string;
  displayRecord: Row | null;
  loading: boolean;
  /** Native detail-query failure; cached record data remains available during refresh failures. */
  loadError: HttpError | null;
  /** The form takes no edit now: it is read-only, or its record has not arrived. */
  formReadOnly: boolean;
  /**
   * The form takes edits, its record's arrival aside: an awaited record counts as
   * taking them unless the form, its resource or its fields rule edits out, so what
   * reserves room for an edit's controls keeps it while the record loads.
   */
  formTakesEdits: boolean;
  formIsDirty: boolean;
  pending: boolean;
  /** The form-level messages of the last refused or failed save, each its own entry; empty when there is none. */
  saveErrors: readonly string[];
  /** Spread on the banner that renders `saveErrors`: a refused save brings the person to it or to its first refused field. */
  saveBanner: ReturnType<typeof useRefusalFocus>["banner"];
  saveConflict: boolean;
  /**
   * The last create was refused because an earlier attempt from this form already wrote a record
   * under its key (`CREATION_KEY_CONFLICT`); the next save is a new draft. The form's banner words it.
   */
  saveKeyConflict: boolean;
  serverFieldErrors: Record<string, readonly string[]>;
  clearServerFieldError: (name: string) => void;
  requiredFieldNames: ReadonlySet<string>;
  linesResource: DataResourceLinesMetadata | null;
  linesField: string | null;
  linesActive: boolean;
  submitForm: (event?: React.BaseSyntheticEvent) => Promise<void>;
  /**
   * What Save would hand its writer now (`FormNextSave`). Save and `nextSave` build it with one function,
   * from the values, the baseline and the changed fields the form holds when it is called. Each reads its
   * own copy of the values: Save the one the form library's submit hands it (its `cloneObject`, which
   * keeps a class instance, and in a browser a file or a file list, as the same object), `nextSave`
   * `cloneFormValue`'s (`structuredClone`, which copies a class instance as a plain object); a record
   * form's values are plain data, for which the two hold the same. What it returns shares nothing with
   * the form, and each call returns a new object: compare it by value, never by identity.
   *
   * Reading it validates nothing, writes nothing, marks nothing dirty and starts no request, and it
   * promises no more than what Save would build. That save may still not happen: the form takes no edit
   * (it is read-only or its record has not arrived: Save throws), a save is already in flight (Save
   * returns), or the rule check, a registered validation, lines another save changed, a running "view
   * as" preview, a revision that is not available or the server stops it.
   *
   * The values, the baseline and the changed fields are read when it is called; what the form saves
   * comes from the render that made the function (the record's fields and what the record permits, the
   * mode, the model's metadata, the create seeds, the lines and their loaded rows). So the latest
   * surface's is always right, and one a reader keeps stays right across edits and discards, and across
   * a save that leaves all of that as it was; once the record arrives, is replaced or permits something
   * else, call the latest surface's. Call it in render or after a pause, never synchronously inside a
   * form subscription callback: during a reset the state it reads is the last render's until the form
   * renders again.
   */
  nextSave: () => FormNextSave;
  discardChanges: () => void;
  applyPatch: (patch: Record<string, unknown>) => Promise<Row | null>;
  patchRecord: (patch: Record<string, unknown>) => void;
  reload: () => Promise<Row | null>;
  afterFieldChange: (field: FieldDescriptor, value: unknown, scope?: string) => void;
  /**
   * Whether the control of `field`, at its full path, is read-only: the form takes no edit, a
   * preview runs, or `fieldControlReadOnly` says so (read-only as declared and resolved, a field the
   * record redacts whatever its widget, or a field the save cannot write whose widget does not write
   * its own value on a saved record).
   */
  fieldReadOnly: (field: FieldDescriptor) => boolean;
  /**
   * Whether the form displays `field`, holding `value`, at all (`formDisplaysField`): on a create,
   * not a field its save cannot write that holds nothing to display, nor, as the form's `status`, one
   * whose control is read-only and holds nothing to display. A control a page renders itself does not ask it.
   */
  displaysField: (field: FieldDescriptor, value: unknown, options?: { status?: boolean }) => boolean;
  startFieldInteraction: (path: string) => void;
  commitFieldInteraction: (path: string) => void;
  /** Register submit-time validation owned by a composed controlled editor. */
  registerFieldValidation: FieldValidationMethods["registerFieldValidation"];
  /** Request departure through the same owner as routed unsaved-change guards. */
  requestLeave: () => Promise<boolean>;
}

/**
 * Whether a route move leaves a record form: its pathname changes, or any search key but the
 * record's tab does. A tab switch keeps the form mounted; the search's other keys (a list's view,
 * filter, sort or page beside the record) are not each known to.
 */
function leavesRecordForm({ current, next }: UnsavedChangesMove): boolean {
  if (current.pathname !== next.pathname) return true;
  const before: Record<string, unknown> = current.search ?? {};
  const after: Record<string, unknown> = next.search ?? {};
  return [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .some((key) => key !== RECORD_TAB_SEARCH_KEY && !deepEqual(before[key], after[key]));
}

/** RHF owns values/baselines/errors; Refine owns resource reads and mutations. */
export function useFormViewSave({
  resource,
  id,
  isCreate,
  readOnly = false,
  dataResource,
  modelMetadata,
  formFields,
  formFieldsFor,
  fieldByName,
  refineFields,
  defaultValues,
  acknowledgedSource,
  onSaved,
  submit,
  createSubmit,
  invalidateModels,
  readOnlyWhen,
  defaultSlugSource,
  onFieldInteractionStart,
  onFieldInteractionCommit,
  onDiscarded,
  revealField,
  t,
}: UseFormViewSaveProps): FormViewSaveSurface {
  const preview = useRuntimeViewAs();
  const { widgets } = useAppRuntime();
  const previewBlocked = Boolean(preview.viewAs || preview.pending);
  const previewBlockedRef = useLatestRef(previewBlocked);
  const toast = useToast();
  const refineResource = refineResourceName(dataResource);
  const updateRevision = Boolean(dataResource?.updateArguments?.some(({ name }) => name === "expected_revision"));
  const saveRevision = Boolean(dataResource?.saveArguments?.some(({ name }) => name === "expected_revision"));
  const selectRevision = (updateRevision || saveRevision)
    && Boolean(dataResource?.fields?.some(({ name }) => name === "revision"));
  const mutationFields = React.useMemo(() => selectRevision && !refineFields.includes("revision")
    ? [...refineFields, "revision"] : refineFields, [refineFields, selectRevision]);
  // A new record of this resource is one draft: its key is minted at the first create attempt and
  // lasts until one is accepted, so a retry, edited or not, is written once. The form names its
  // draft under the surface hosting it (a relation field's create of another form), and holds it.
  const idempotencyKeys = useIdempotencyKeys();
  const provider = dataResource?.schemaName ?? "default";
  const draftScope = useDraftScope(isCreate ? `record.create:${provider}:${resource}` : `record.edit:${provider}:${resource}:${String(id)}`);
  useIdempotencyDraftOwner(isCreate ? draftScope : null, `the create form of ${resource}`);
  const emptyValues = React.useMemo(
    () => emptyDraft(formFields, defaultValues),
    [defaultValues, formFields],
  );
  const createSeedNames = React.useMemo<ReadonlySet<string>>(
    () => new Set(Object.keys(defaultValues ?? {})),
    [defaultValues],
  );
  const manualSlugFieldsRef = React.useRef<Set<string>>(new Set());
  const [presetMemory] = React.useState(createPresetMemory);
  const localAcknowledgementRef = React.useRef<{
    previous: FormValues | null;
    accepted: FormValues;
  } | null>(null);
  const mounted = React.useRef(true);
  React.useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const submittingRef = React.useRef(false);
  const submitOwner = submit ?? (isCreate ? createSubmit : undefined);
  const requiredFieldNames = React.useMemo<ReadonlySet<string>>(() => {
    if (!isCreate) return new Set();
    const required = new Set(modelMetadata?.resource.requiredCreateFields ?? []);
    return new Set(
      formFields
        .filter((field) => (field.required || required.has(field.name)) && !field.readOnly)
        .map((field) => field.name),
    );
  }, [formFields, isCreate, modelMetadata]);
  // What the form's mode lets its save write, a field or its root: the model's create fields on a
  // create, its updatable fields on an edit; none without metadata.
  const modeWritableNames = React.useMemo<ReadonlySet<string> | null>(() => {
    const writable = isCreate
      ? modelMetadata?.resource.createFields
      : modelMetadata
        ? formFields
            .map((field) => field.name)
            .filter((fieldName) => fieldUpdatable(modelMetadata, fieldName))
        : undefined;
    return writable ? new Set(writable) : null;
  }, [formFields, isCreate, modelMetadata]);
  const queryClient = useQueryClient();
  const canonicalInvalidateModels = useCanonicalResourceModelLabels(invalidateModels);
  const dependentInvalidations = useResourceInvalidates(canonicalInvalidateModels);
  const authoredInvalidateModels = React.useMemo(() => [...new Set([
    ...(!submitOwner && dataResource ? [dataResource.modelLabel] : []), ...canonicalInvalidateModels,
  ])], [dataResource, canonicalInvalidateModels, submitOwner]);
  const { keys } = useKeys();
  const { identifier } = useResourceParams({ resource: refineResource });
  const detailKey = React.useMemo(() => keys().data(dataResource?.schemaName ?? "default")
    .resource(identifier ?? "").action("one").id(id ?? "")
    .params({ fields: mutationFields }).get(), [dataResource?.schemaName, id, identifier, keys, mutationFields]);
  const nativeReadEnabled = acknowledgedSource === undefined && !isCreate && Boolean(id && dataResource?.roots.detail);
  const read = useOne<RowRecord, HttpError>({
    resource: refineResource,
    id: id ?? undefined,
    dataProviderName: dataResource?.schemaName,
    meta: { fields: mutationFields },
    queryOptions: {
      queryKey: detailKey,
      enabled: nativeReadEnabled,
    },
  });
  const record = acknowledgedSource !== undefined
    ? acknowledgedSource.record
    : read.result ?? null;
  const displayRecord = record;
  const recordFields = React.useMemo(
    () => formFieldsFor?.(displayRecord) ?? formFields,
    [displayRecord, formFields, formFieldsFor],
  );
  // The paths the save writes (`formWritablePaths`), every one under a custom submit owner.
  const writableFieldNames = React.useMemo(
    () => formWritablePaths(recordFields, modeWritableNames, Boolean(submitOwner)),
    [modeWritableNames, recordFields, submitOwner],
  );
  // Two facts, two sets. The record's own fields (`formFieldsFor`) its save can write (`pathWritable`):
  // what the save sends reads these, its resets of hidden fields included, and so do which declared
  // preset applies and the draft's drop and restore of hidden fields. A field only an undisplayed group
  // declares and one the mode cannot write are not among them, so they are never sent, though a preset
  // may still write them into the form's values. A redacted one is among them, and is sent only when the
  // form itself wrote it; a structured one, once the form wrote one member, goes whole, its untouched
  // members as the form loaded them (a limit: they may be the record's placeholders). Of those, the ones
  // the record does not redact (`fieldIsRedacted`): the check, the member marks and the banner's claim
  // read these. Where several displayed declarations name one, the first saves, the page's own before a
  // contribution's. Whether a control takes edits and whether a create displays a field read the same
  // owners (`fieldReadOnly`, `displaysField`).
  const saveFields = React.useMemo(
    () => recordFields.filter((field) => pathWritable(field.name, writableFieldNames)),
    [recordFields, writableFieldNames],
  );
  const checkFields = React.useMemo(
    () => saveFields.filter((field) => !fieldIsRedacted(displayRecord, field.name)),
    [displayRecord, saveFields],
  );
  const editBasisRecordRef = React.useRef<Row | null>(displayRecord);
  const loading = acknowledgedSource?.loading ?? (nativeReadEnabled && (read.query.isPending || read.query.isFetching));
  const loadError = acknowledgedSource === undefined ? read.query.error : null;
  const reload = React.useCallback(async (): Promise<Row | null> => {
    if (acknowledgedSource !== undefined) {
      return await acknowledgedSource.reload?.() ?? null;
    }
    const result = await read.query.refetch();
    return result.error ? null : result.data?.data ?? null;
  }, [acknowledgedSource, read.query.refetch]);
  const create = useCreate<RowRecord, HttpError, FormValues>({
    resource: refineResource,
    dataProviderName: dataResource?.schemaName,
    meta: { fields: mutationFields },
    invalidates: ["list", "many"],
    successNotification: false,
    errorNotification: false,
  });
  const update = useUpdate<RowRecord, HttpError, FormValues>({
    resource: refineResource,
    dataProviderName: dataResource?.schemaName,
    meta: { fields: mutationFields },
    invalidates: ["list", "many", "detail"],
    successNotification: false,
    errorNotification: false,
  });
  const linesResource = dataResource?.linesResource ?? null;
  const linesConfig = React.useMemo(
    () => (linesResource ? lineDiffConfig(linesResource) : null),
    [linesResource],
  );
  const linesField = linesResource?.field ?? null;
  const saveOperation = useSaveOperation(dataResource);
  const resourceSave = useAngeeResourceSave(saveOperation.target, {
    document: saveOperation.document,
  });
  const invalidate = useInvalidate();
  const resourceInvalidates = useResourceInvalidates(dataResource ? [dataResource.modelLabel] : undefined);
  const linesActive =
    linesConfig !== null &&
    linesField !== null &&
    (isCreate
      ? Boolean(dataResource?.roots.create || submit || createSubmit)
      : saveOperation.target !== null || Boolean(submit));
  const seedLineRows = React.useMemo(
    () =>
      linesActive && linesConfig && linesField
        ? recordLinesToRows(record?.[linesField], linesConfig)
        : null,
    [linesActive, linesConfig, linesField, record],
  );
  const linesSeed = React.useCallback(
    (rows: readonly Row[] | null | undefined): LinesSeed | undefined =>
      linesActive && linesField && rows ? { field: linesField, rows } : undefined,
    [linesActive, linesField],
  );
  const rowsFromRecord = React.useCallback(
    (source: Row | null | undefined): readonly Row[] | null =>
      linesActive && linesConfig && linesField
        ? recordLinesToRows(source?.[linesField], linesConfig)
        : null,
    [linesActive, linesConfig, linesField],
  );
  const invalidateResource = React.useCallback(async () => {
    if (!dataResource) return;
    await Promise.all(resourceInvalidates.map((target) => invalidate(target)));
    // The successful-submit path below also invalidates authored reads for this model.
  }, [dataResource, invalidate, resourceInvalidates]);

  const values = React.useMemo(() => {
    if (acknowledgedSource !== undefined || isCreate) {
      const sourceValues = acknowledgedSource !== undefined ? acknowledgedSource.values : emptyValues;
      const local = localAcknowledgementRef.current;
      if (local && replaceEqualDeep(local.previous, sourceValues) === local.previous) {
        return local.accepted;
      }
      localAcknowledgementRef.current = null;
      return sourceValues ?? emptyValues;
    }
    return record
      ? recordToValues(record, formFields, linesSeed(seedLineRows)) : emptyValues;
  }, [acknowledgedSource, emptyValues, formFields, isCreate, linesSeed, record, seedLineRows]);
  const recordUnavailable = !isCreate && record == null;
  const formTakesEdits = React.useMemo(
    () => !(
      readOnly ||
      // A form is read-only for a record that projects its permissions without
      // `write`, whether or not the model's metadata declares the field.
      (!isCreate && record != null
        && (Array.isArray(record.permissions) || Boolean(modelMetadata?.fields.permissions))
        && !holdsPermission(record, "write")) ||
      (!isCreate && record != null && Boolean(readOnlyWhen?.(record))) ||
      (!submitOwner &&
        !Boolean(isCreate ? dataResource?.roots.create : dataResource?.roots.update)) ||
      (formFields.length > 0 && formFields.every((field) => field.readOnly))
    ),
    [dataResource, formFields, isCreate, modelMetadata, readOnly, record, readOnlyWhen, submitOwner],
  );
  const formReadOnly = recordUnavailable || !formTakesEdits;
  const { registerFieldValidation, validateFields } = useFieldValidation();
  const form = useForm<FormValues>({
    defaultValues: emptyValues,
    shouldUnregister: false,
    resolver: (formValues) => {
      // The check asks the fields the save writes for this record that the record does not redact.
      const errors = formRuleErrors(formValues, checkFields, requiredFieldNames, t);
      validateFields(formValues, (name, error) => set(errors, name, error));
      markMemberErrors(errors, visibleFields(checkFields, formValues));
      return Object.keys(errors).length
        ? { values: {}, errors }
        : { values: formValues, errors: {} };
    },
  });
  const { reset, resetDefaultValues, clearErrors, getFieldState, setValue } = form;
  const history = useFormHistory(form, { readOnly: formReadOnly });
  const { reset: resetHistory, start: startHistory, commit: commitHistory } = history;
  const activeFieldInteractions = React.useRef(new Set<string>());
  const { dirtyFields } = form.formState;
  const syncRecordValues = React.useCallback((next: FormValues, lineBaseline?: unknown) => {
    // RHF merges dirty paths by index. A full-list line mutation is atomic, so
    // never let remote row ordering supply cells to a dirty local row array.
    const keepLines = linesActive && linesField !== null && form.getFieldState(linesField).isDirty;
    const baseline = keepLines ? {
      ...next,
      [linesField]: lineBaseline ?? form.formState.defaultValues?.[linesField],
    } : next;
    // Metadata projections may be referentially new with the same wire values.
    // Compare against RHF's defaults, not a second stored baseline.
    if (replaceEqualDeep(form.formState.defaultValues, baseline) === form.formState.defaultValues) return;
    if (keepLines) {
      // Update clean scalar controls without publishing a field-array reset.
      for (const [name, value] of Object.entries(next)) {
        if (name !== linesField && !form.getFieldState(name).isDirty) setValue(name, value);
      }
    } else {
      const arrays = dirtyArrayValues(form.getValues(), (path) => form.getFieldState(path).isDirty);
      reset(next, { keepDirtyValues: true, keepDirty: true });
      for (const [path, value] of arrays) setValue(path, value, { shouldDirty: true });
    }
    resetDefaultValues(baseline, { keepIsValid: true });
    // Whole-value undo frames cannot restore fields from an older accepted baseline.
    resetHistory();
    activeFieldInteractions.current.clear();
  }, [form, linesActive, linesField, reset, resetDefaultValues, resetHistory, setValue]);
  const lineDraftDirty = Boolean(!isCreate && linesActive && linesField && dirtyFields[linesField]);
  // Replay a held remote array when the user undoes the last local line edit.
  React.useEffect(() => {
    syncRecordValues(values);
  }, [lineDraftDirty, syncRecordValues, values]);
  const serverFieldErrors = React.useMemo(() => serverErrorsFromForm(form.formState.errors), [form.formState.errors]);
  const saveErrors = formErrorsFromForm(form.formState.errors);
  // The record form never locks its fields: a refusal's focus lands once the answer renders, on
  // the form library's timer.
  const { banner: saveBanner, focus: focusSaveRefusal } = useRefusalFocus(saveErrors, { form });
  const saveConflict = form.formState.errors.root?.server?.type === "conflict";
  const saveKeyConflict = form.formState.errors.root?.server?.type === "creationKeyConflict";
  const clearServerFieldError = React.useCallback((name: string) => clearErrors(name), [clearErrors]);
  const customSubmit = useMutation({
    mutationFn: async ({ data, lines, submitted, baseline, idempotencyKey }: { data: FormValues; lines: LineDiff | null; submitted: FormValues; baseline: FormValues; idempotencyKey: string | null }) => {
      if (!submitOwner) throw new Error("No custom form submission is configured.");
      return submitOwner(data, {
        resource, id: id ?? null, isCreate, record: displayRecord,
        baselineRecord: editBasisRecordRef.current, lines,
        idempotencyKey,
        values: submitted, baselineValues: baseline,
      });
    },
  });
  const formIsDirty = form.formState.isDirty;
  const pending = create.mutation.isPending || update.mutation.isPending || customSubmit.isPending || resourceSave.fetching || form.formState.isSubmitting;
  const formIsDirtyRef = React.useRef(formIsDirty);
  React.useEffect(() => {
    formIsDirtyRef.current = formIsDirty;
  }, [formIsDirty]);
  React.useEffect(() => {
    if (!formIsDirty) editBasisRecordRef.current = displayRecord;
  }, [displayRecord, formIsDirty]);
  const isDirtyNow = React.useCallback(() => formIsDirtyRef.current, []);
  const requestLeave = useUnsavedChangesNavigationGuard({
    isDirty: formIsDirty,
    isDirtyNow,
    readOnly: formReadOnly,
    leaves: leavesRecordForm,
  });

  // A create carries its draft's key when its writer takes one: a custom owner always receives it,
  // but only the form's own create root, when it takes the key, is known to send it, so only there
  // does a lost answer promise that sending again sends it once.
  const sendsKey = isCreate && !submitOwner && Boolean(dataResource?.createArguments?.some(({ name }) => name === "idempotency_key"));
  const keyed = sendsKey || (isCreate && Boolean(submitOwner));
  const runSubmit = React.useCallback(
    async (data: FormValues, lines: LineDiff | null = null, submitted: FormValues = data): Promise<FormSubmitResult<Row | FormSubmitAcknowledgement>> => {
      // One keyed write per attempt, lines included: the draft's key goes with it.
      const write = async (idempotencyKey: string | null): Promise<FormSubmitResult<Row | FormSubmitAcknowledgement>> => {
        if (submitOwner) {
          return customSubmit.mutateAsync({
            data,
            lines,
            submitted,
            baseline: (form.formState.defaultValues ?? {}) as FormValues,
            idempotencyKey,
          });
        } else {
          const useSave = !isCreate && lines?.hasChanges && id != null && saveOperation.target !== null;
          const rootArguments: MutationRootArguments = {};
          if (!isCreate && (useSave ? saveRevision : updateRevision)) {
            const revision = editBasisRecordRef.current?.revision;
            if (typeof revision !== "number" || !Number.isInteger(revision)) {
              throw new Error(t("form.revisionUnavailable"));
            }
            rootArguments.expected_revision = revision;
          }
          if (isCreate && idempotencyKey) {
            rootArguments.idempotency_key = idempotencyKey;
          }
          let saved: Row | null;
          if (useSave) {
            saved = await resourceSave.save({ pk: id, patch: data, lines: lines.payload, ...rootArguments });
            if (saved) await invalidateResource();
          } else {
            const mutationMeta = Object.keys(rootArguments).length
              ? { meta: { fields: mutationFields, gqlVariables: rootArguments } } : {};
            const response = isCreate
              ? await create.mutateAsync({
                  ...mutationMeta,
                  values: lines?.hasChanges && linesField
                    ? { ...data, [linesField]: { data: lines.payload } }
                    : data,
                })
              : await update.mutateAsync({ id: id as BaseKey, values: data, ...mutationMeta });
            saved = response?.data ?? null;
          }
          return savedFormSubmitResult(saved, t("form.genericSaveError"));
        }
      };
      // A refused create keeps the draft and its key; an accepted one ends it.
      const result = keyed ? await idempotencyKeys.send(draftScope, write, formSubmitOutcome) : await write(null);
      if (result.status === "ok") {
        // Started, not awaited: the form shows its own record, which the write's answer carries. A read
        // elsewhere (a list of a dependent model, the record's chrome) that is slow, fails or never
        // answers must not hold the confirmation back or keep Save from the next write; its failure is
        // its own query's to report.
        void Promise.all([
          ...dependentInvalidations.map((target) => invalidate(target)),
          ...(authoredInvalidateModels.length ? [invalidateAuthoredQueries(queryClient, authoredInvalidateModels)] : []),
        ]).catch(() => undefined);
      }
      return result;
    },
    [
      customSubmit.mutateAsync,
      authoredInvalidateModels,
      dependentInvalidations,
      invalidate,
      queryClient,
      dataResource,
      mutationFields,
      updateRevision,
      saveRevision,
      form.formState.defaultValues,
      t,
      create.mutateAsync,
      update.mutateAsync,
      id,
      invalidateResource,
      isCreate,
      linesField,
      resource,
      resourceSave,
      saveOperation.target,
      submitOwner,
      idempotencyKeys,
      draftScope,
      keyed,
    ],
  );
  /** The fields the form shows for its values now, in its order, then its lines: what a refusal or a failed check can name. */
  const shownNames = React.useCallback(
    () => [...shownFields(checkFields, form.getValues()).map((field) => field.name), ...(linesField ? [linesField] : [])],
    [checkFields, form, linesField],
  );
  const readSubmitResult = React.useCallback((result: FormSubmitResult<Row | FormSubmitAcknowledgement>) => {
    if (!mounted.current) return null;
    // Every refused field's message is set at its path, so a control a page renders itself shows
    // it; the summary calls highlighted only the checked fields whose descriptor is neither hidden
    // nor excluded by its condition, and gives any other its own message, one only a group the
    // record does not display declares, one the save cannot write and one the record redacts
    // among them.
    return applyFormErrors(form, result, {
      shownFieldNames: shownNames(),
      fieldSummary: (errors) => fieldValidationSummary(errors, fieldByName, t),
      fallback: t("form.genericSaveError"),
      focus: (field) => {
        if (field !== undefined) revealField?.(field);
        focusSaveRefusal(field);
      },
    }) ? null : result;
  }, [fieldByName, focusSaveRefusal, form, revealField, shownNames, t]);
  const commitSavedRecord = React.useCallback(
    (saved: Row, options: {
      submitted?: FormValues;
      submittedFields?: readonly string[];
      /** What the save wrote for fields a preset hid, which the submitted draft does not hold. */
      writtenResets?: ReadonlyMap<string, unknown>;
      acceptedValues?: FormValues;
      reconcile?: FormSubmitAcknowledgement["reconcile"];
      createdLines?: boolean;
      notify?: boolean;
      message?: string;
      refetchPartial?: boolean;
    } = {}): void => {
      // A mutation response is a patch over the latest cache, which may have
      // refreshed while the write was pending. Accepted submitted values fill
      // response omissions until canonical refetch; unsubmitted fields stay current.
      const submitted = Object.fromEntries((options.submittedFields ?? [])
        .map((name) => [name, options.submitted?.[name]]));
      const acceptedPatch = { ...submitted, ...saved };
      // What the save wrote for a hidden field fills its path where the response omits it,
      // inside whatever the cached row holds around that path.
      const acceptedRow = (cached: Row | null | undefined): Row => {
        const row: Row = { ...cached, ...acceptedPatch };
        for (const [path, reset] of options.writtenResets ?? []) {
          if (get(saved, path) !== undefined) continue;
          const head = path.split(".", 1)[0]!;
          if (head !== path) row[head] = structuredClone(row[head] ?? cached?.[head]);
          set(row, path, structuredClone(reset));
        }
        return row;
      };
      const accepted = acknowledgedSource !== undefined
        ? { ...(record ?? {}), ...saved }
        : isCreate ? acceptedPatch : queryClient.setQueryData<GetOneResponse<RowRecord>>(
        detailKey,
        (current) => ({ ...current, data: acceptedRow(current?.data ?? record) }),
      )?.data ?? acceptedPatch;
      editBasisRecordRef.current = accepted;
      if (!mounted.current) return;
      const savedValues = options.acceptedValues
        ?? recordToValues(accepted, formFields, linesSeed(rowsFromRecord(accepted)));
      if (isCreate || (options.acceptedValues && acknowledgedSource !== undefined)) {
        // Until the source changes, a descriptor rerender must retain the save
        // acknowledgement instead of replaying the original create defaults.
        localAcknowledgementRef.current = {
          previous: acknowledgedSource !== undefined ? acknowledgedSource.values : emptyValues,
          accepted: savedValues,
        };
      }
      const currentValues = form.getValues();
      const savedLines = linesField ? savedValues[linesField] : undefined;
      const submittedLines = linesField ? options.submitted?.[linesField] : undefined;
      const currentLines = linesField ? currentValues[linesField] : undefined;
      const reconciledLines = options.createdLines && linesConfig
        && Array.isArray(savedLines) && Array.isArray(submittedLines) && Array.isArray(currentLines)
          ? reconcileAcceptedLineRows(savedLines, submittedLines, currentLines, linesConfig)
          : null;
      if (options.acceptedValues && options.submitted) {
        const reconciled = options.reconcile?.({
          accepted: options.acceptedValues,
          submitted: options.submitted,
          current: currentValues,
        });
        if (reconciled) {
          reset(options.acceptedValues, { keepIsValid: true });
          reset(reconciledLines && linesField
            ? { ...reconciled, [linesField]: reconciledLines }
            : reconciled, { keepDefaultValues: true, keepIsValid: true });
        } else {
          resetDefaultValues(options.submitted, { keepIsValid: true });
          syncRecordValues(options.acceptedValues,
            reconciledLines ? savedLines : undefined);
          if (reconciledLines && linesField) {
            setValue(linesField, reconciledLines, { shouldDirty: true });
          }
        }
      } else {
        // The full pre-request draft includes values omitted from the wire patch.
        // RHF then preserves only edits made while this submission was pending.
        resetDefaultValues(options.submitted ?? form.formState.defaultValues ?? {}, { keepIsValid: true });
        syncRecordValues(savedValues, linesField
          ? reconciledLines ? savedLines
            : options.createdLines ? submitted[linesField] : savedLines
          : undefined);
        if (reconciledLines && linesField) {
          setValue(linesField, reconciledLines, { shouldDirty: true });
        }
      }
      const observedNames = new Set([
        ...formFields.map((field) => field.name),
        ...Object.keys(form.getValues()),
        ...Object.keys(savedValues),
      ]);
      formIsDirtyRef.current = [...observedNames].some((name) => form.getFieldState(name).isDirty)
        || Boolean(linesField && form.getFieldState(linesField).isDirty);
      resetHistory();
      activeFieldInteractions.current.clear();
      if (isCreate) manualSlugFieldsRef.current.clear();
      if (options.message || options.notify) {
        toast.success({ title: options.message || t(isCreate ? "form.createSuccess" : "form.updateSuccess") });
      }
      if (options.notify) {
        onSaved?.(accepted);
      }
      // Fetch canonical server values when the response omitted any selected field.
      if (options.refetchPartial !== false && !isCreate && (
        formFields.some((field) => !Object.hasOwn(saved, field.name))
        || (linesActive && linesField !== null && !Object.hasOwn(saved, linesField))
      )) reload();
    },
    [acknowledgedSource, detailKey, emptyValues, form, formFields, isCreate, linesActive, linesConfig, linesField, linesSeed, onSaved, queryClient, record, reload, reset, resetDefaultValues, resetHistory, rowsFromRecord, setValue, syncRecordValues, t, toast],
  );
  // What the save writes for `value`, its resets of hidden fields and its lines diff, with the loaded
  // rows that diff reads: the one place Save and `nextSave` build it. The baseline and the changed fields
  // are the form's when it is called; everything else is this render's.
  const saveFor = React.useCallback((value: FormValues) => {
    const baseline = (form.formState.defaultValues ?? {}) as FormValues;
    const saveOptions = { isCreate, seededFieldNames: createSeedNames, writableFields: writableFieldNames };
    const resets = hiddenResetWrites(value, saveFields, { ...saveOptions, baseline });
    const data = mutationData(value, saveFields, {
      ...saveOptions,
      dirtyFields: form.formState.dirtyFields as Record<string, unknown>,
      fieldMetadata: modelMetadata?.fields,
      hiddenResets: resets,
    });
    const lineBaseline = linesActive && linesConfig && linesField ? baselineLineRows(baseline, linesField, seedLineRows) : null;
    const lines = lineBaseline && linesConfig && linesField
      ? diffLines(lineBaseline, (value[linesField] as Row[] | undefined) ?? [], linesConfig)
      : null;
    return { data, resets, lines, lineBaseline };
  }, [createSeedNames, form, isCreate, linesActive, linesConfig, linesField, modelMetadata, saveFields, seedLineRows, writableFieldNames]);
  // Its own copy of the values (Save reads the submit's), so what it returns shares nothing with them.
  const nextSave = React.useCallback((): FormNextSave => {
    const { data, lines } = saveFor(cloneFormValue(form.getValues()));
    return { data, lines };
  }, [form, saveFor]);
  const submitValues = React.useCallback(
    async (value: FormValues) => {
      if (submittingRef.current || previewBlockedRef.current) return;
      clearErrors();
      if (formReadOnly) {
        throw new Error(`Resource mutation for "${resource}" is disabled.`);
      }
      if (!dataResource) {
        throw new Error(`Resource metadata for "${resource}" is not available.`);
      }
      const { data, resets, lines: linesDiff, lineBaseline } = saveFor(value);
      if (!isCreate && linesDiff?.hasChanges && lineBaseline && linesConfig && linesField) {
        const latest = queryClient.getQueryData<GetOneResponse<RowRecord>>(detailKey)?.data ?? record;
        // Full-list writes implicitly delete omitted IDs. Refuse a stale draft
        // instead of deleting remote additions or resubmitting newly saved rows
        // whose generated IDs cannot be matched to a concurrently edited draft.
        if (lineBaseline.some((row) => row[linesConfig.idField] == null)
          || !sameObservedLines(lineBaseline, rowsFromRecord(latest) ?? [], linesConfig)) {
          applyFormErrors(form, { status: "conflict", message: t("form.linesChanged"), field: linesField }, { shownFieldNames: [linesField] });
          return;
        }
      }
      submittingRef.current = true;
      try {
        const result = readSubmitResult(await runSubmit(data, linesDiff, value)
          .catch((cause) => formSubmitError(cause, t("form.genericSaveError"),
            sendsKey ? t("form.answerLostResend") : keyed ? t("form.answerLostMaybeSent") : undefined)));
        if (result) {
          const response = result.data;
          const acknowledgement = isFormSubmitAcknowledgement(response) ? response : undefined;
          const saved: Row = acknowledgement ? acknowledgement.record : response as Row;
          commitSavedRecord(saved, {
            submitted: value,
            submittedFields: acknowledgement
              ? []
              : [...Object.keys(data), ...(linesDiff && linesField ? [linesField] : [])],
            ...(acknowledgement ? { acceptedValues: acknowledgement.values } : { writtenResets: resets }),
            ...(acknowledgement?.reconcile ? { reconcile: acknowledgement.reconcile } : {}),
            createdLines: Boolean(linesDiff?.created.length),
            notify: true,
            message: result.message,
          });
        }

      } finally {
        submittingRef.current = false;

      }
    },
    [
      clearErrors,
      dataResource,
      formReadOnly,
      isCreate,
      keyed,
      previewBlockedRef,
      sendsKey,
      commitSavedRecord,
      linesConfig,
      linesField,
      resource,
      runSubmit,
      readSubmitResult,
      saveFor,
      t,
      form,
      queryClient,
      detailKey,
      rowsFromRecord,
      record,
    ],
  );
  // The form's own check failed: its first errored field, the lines among them, is brought into sight
  // as a refusal's is (`revealField`); the form library then focuses, on its own deferred timer, the
  // first errored control in its own mount order.
  const submitForm = form.handleSubmit(submitValues, (errors) => {
    const first = shownNames().find((name) => get(errors, name) !== undefined);
    if (first !== undefined) revealField?.(first);
  });
  const applyPatch = React.useCallback(
    async (patch: Record<string, unknown>): Promise<Row | null> => {
      if (previewBlockedRef.current) return null;
      if (id == null) throw new Error("No open record to update.");
      if (formReadOnly) {
        throw new Error(`Resource mutation for "${resource}" is disabled.`);
      }
      const result = readSubmitResult(await runSubmit(patch));
      let saved: Row | null = null;
      if (result) {
        const response = result.data;
        saved = isFormSubmitAcknowledgement(response) ? response.record : response;
        commitSavedRecord(saved, {
          ...(isFormSubmitAcknowledgement(response) ? { acceptedValues: response.values } : {}),
          message: result.message,
        });
        clearErrors();
      }
      return saved;
    },
    [clearErrors, commitSavedRecord, formReadOnly, id, resource, runSubmit, readSubmitResult],
  );
  const patchRecord = React.useCallback((patch: Record<string, unknown>): void => {
    if (record) { commitSavedRecord(patch, { refetchPartial: false }); clearErrors(); }
  }, [clearErrors, commitSavedRecord, record]);

  const afterFieldChange = React.useCallback(
    (field: FieldDescriptor, value: unknown, scope = ""): void => {
      const scoped = (name: string) => scope ? `${scope}.${name}` : name;
      presetMemory.noteEdit(scoped(field.name));
      clearErrors(scoped(field.name));
      // What the form compares itself against: a preset may give a sibling its loaded value back.
      const loaded = form.formState.defaultValues;
      // The preset the form declares for this field is the one its save reads again (see
      // `hiddenResetWrites`); a descriptor's own preset still seeds, but drops nothing.
      const declared = saveFields.find((candidate) => candidate.name === scoped(field.name));
      const own = declared && resolveField(declared, form.getValues());
      const preset = own?.prefill ? own : field;
      const written = seedPreset(
        preset,
        value,
        (scope ? form.getValues(scope) : form.getValues()) as Row,
        ((scope ? get(loaded, scope) : loaded) ?? undefined) as Row | undefined,
        {
          isCreate,
          memory: presetMemory,
          at: scoped,
          read: (path) => ({ dirty: getFieldState(path).isDirty, value: form.getValues(path) }),
          write: (path, seed, dirties) => setValue(path, seed, { shouldDirty: dirties, shouldTouch: true }),
        },
      );
      // The draft holds only the fields visible for its new values, as it was loaded (`emptyDraft`).
      // A dialog keeps this rule by what its list does not show (DescriptorFieldList's
      // `unregisterHidden`); this form's controls also unmount for tabs and sections, so it computes
      // the hidden paths from its model instead, and makes the same two calls.
      if (written.length && preset === own) {
        const hidden = hiddenDraftPaths(form.getValues(), saveFields, written);
        if (hidden.length) form.unregister(hidden, { keepDefaultValue: true });
      }
      // A field dropped from the draft that shows again holds its loaded value.
      for (const [path, restored] of missingDraftValues(form.getValues(), saveFields, (loaded ?? {}) as FormValues)) {
        setValue(path, restored, { shouldDirty: true });
      }
      if (fieldWidgetId(field) === "slug") {
        manualSlugFieldsRef.current.add(field.name);
        return;
      }
      if (!isCreate) return;
      for (const slugField of formFields) {
        if (fieldWidgetId(slugField) !== "slug") continue;
        if (manualSlugFieldsRef.current.has(slugField.name)) continue;
        if ((slugField.slugFrom ?? defaultSlugSource) !== field.name) continue;
        setValue(slugField.name, slugify(value), {
          shouldDirty: true,
          shouldTouch: true,
        });
      }
    },
    [clearErrors, defaultSlugSource, form, formFields, getFieldState, isCreate, presetMemory, saveFields, setValue],
  );
  const fieldReadOnly = React.useCallback(
    (field: FieldDescriptor): boolean => formReadOnly || previewBlocked || fieldControlReadOnly(field, {
      writable: pathWritable(field.name, writableFieldNames), redacted: fieldIsRedacted(displayRecord, field.name), isCreate,
      writesOwnValue: Boolean(asWidgetDefinition(widgets[fieldWidgetId(field)])?.writesOwnValue),
    }),
    [displayRecord, formReadOnly, isCreate, previewBlocked, widgets, writableFieldNames],
  );
  const displaysField = React.useCallback(
    (field: FieldDescriptor, value: unknown, { status = false }: { status?: boolean } = {}): boolean => formDisplaysField(value, {
      writable: pathWritable(field.name, writableFieldNames), isCreate,
      readOnlyStatus: status && fieldReadOnly(field),
    }),
    [fieldReadOnly, isCreate, writableFieldNames],
  );
  const discardChanges = React.useCallback(() => {
    if (isCreate) {
      localAcknowledgementRef.current = null;
      // The person gives the new record up: its draft ends, unless an attempt may have created it.
      idempotencyKeys.discard(draftScope);
    }
    presetMemory.clear();
    reset(isCreate ? emptyValues : values, { keepDirtyValues: false, keepDirty: false });
    resetHistory();
    activeFieldInteractions.current.clear();
    formIsDirtyRef.current = false;
    editBasisRecordRef.current = displayRecord;
    onDiscarded?.();
  }, [draftScope, displayRecord, emptyValues, idempotencyKeys, isCreate, onDiscarded, presetMemory, reset, resetHistory, values]);
  const startFieldInteraction = React.useCallback(
    (path: string) => {
      startHistory(path);
      if (activeFieldInteractions.current.has(path)) return;
      activeFieldInteractions.current.add(path);
      onFieldInteractionStart?.(path);
    },
    [onFieldInteractionStart, startHistory],
  );
  const commitFieldInteraction = React.useCallback(
    (path: string) => {
      if (!activeFieldInteractions.current.delete(path)) return;
      commitHistory(path);
      onFieldInteractionCommit?.(path);
    },
    [onFieldInteractionCommit, commitHistory],
  );
  return {
    form: { ...form, registerFieldValidation },
    history,
    draftScope,
    displayRecord,
    loading,
    loadError,
    formReadOnly,
    formTakesEdits,
    formIsDirty,
    pending,
    saveErrors,
    saveBanner,
    saveConflict,
    saveKeyConflict,
    serverFieldErrors,
    clearServerFieldError,
    requiredFieldNames,
    linesResource,
    linesField,
    linesActive,
    submitForm,
    nextSave,
    discardChanges,
    applyPatch,
    patchRecord,
    reload,
    afterFieldChange,
    fieldReadOnly,
    displaysField,
    startFieldInteraction,
    commitFieldInteraction,
    registerFieldValidation,
    requestLeave,
  };
}

function isFormSubmitAcknowledgement(value: Row | FormSubmitAcknowledgement): value is FormSubmitAcknowledgement {
  return value.kind === "form-submit-acknowledgement" && "record" in value && "values" in value;
}

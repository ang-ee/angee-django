import * as React from "react";
import {
  fieldUpdatable,
  refineResourceName,
  rowPublicId,
  type DataResourceLinesMetadata,
  type DataResourceMetadata,
  type ModelMetadata,
  type Row,
} from "@angee/metadata";
import { useAngeeResourceSave } from "@angee/refine";
import {
  useInvalidate,
  type BaseKey,
  type BaseRecord,
  type Fields,
  type HttpError,
} from "@refinedev/core";
import { useForm as useRefineForm } from "@refinedev/react-hook-form";

import { useLatestRef } from "../lib/use-latest-ref";
import type { UiTranslate } from "../i18n";
import { slugify } from "../widgets";
import { fieldWidgetId, type FieldDescriptor } from "./page";
import {
  diffLines,
  lineDiffConfig,
  recordLinesToRows,
  type LineDiff,
} from "./editable-lines";
import {
  baselineLineRows,
  emptyDraft,
  fieldValidationSummary,
  formValuesEqual,
  mutationData,
  recordToValues,
  type FormValues,
  type LinesSeed,
} from "./form-view-model";
import { useSaveOperation } from "./resource-operations";
import { validationErrorsFromError } from "./validation-errors";
import { useUnsavedChangesNavigationGuard } from "./use-unsaved-changes-navigation-guard";

type RowRecord = BaseRecord & Row;

export interface FormSubmitContext {
  resource: string;
  id: string | null;
  isCreate: boolean;
  record: Row | null;
  lines: LineDiff | null;
}

export type FormSubmit = (
  data: Record<string, unknown>,
  context: FormSubmitContext,
) => Row | null | undefined | Promise<Row | null | undefined>;

export type FormViewForm = ReturnType<
  typeof useRefineForm<RowRecord, HttpError, FormValues>
>;

export interface UseFormViewSaveProps {
  resource: string;
  id?: string | null;
  isCreate: boolean;
  dataResource: DataResourceMetadata | null;
  modelMetadata: ModelMetadata | null;
  formFields: readonly FieldDescriptor[];
  fieldByName: ReadonlyMap<string, FieldDescriptor>;
  refineFields: Fields;
  defaultValues?: Record<string, unknown>;
  onSaved?: (row: Row) => void;
  submit?: FormSubmit;
  createSubmit?: FormSubmit;
  defaultSlugSource?: string;
  t: UiTranslate;
}

export interface FormViewSaveSurface {
  form: FormViewForm;
  displayRecord: Row | null;
  loading: boolean;
  formReadOnly: boolean;
  formIsDirty: boolean;
  pending: boolean;
  saveError: string | null;
  serverFieldErrors: Record<string, readonly string[]>;
  clearServerFieldError: (name: string) => void;
  requiredFieldNames: ReadonlySet<string>;
  linesResource: DataResourceLinesMetadata | null;
  linesField: string | null;
  linesActive: boolean;
  submitForm: (event?: React.BaseSyntheticEvent) => Promise<void>;
  discardChanges: () => void;
  applyPatch: (patch: Record<string, unknown>) => Promise<Row | null>;
  patchRecord: (patch: Record<string, unknown>) => void;
  reload: () => void;
  afterFieldChange: (field: FieldDescriptor, value: unknown) => void;
  fieldReadOnly: (field: FieldDescriptor) => boolean;
}

/** Own refine form wiring, mutation/diff, server errors, and record reseeding. */
export function useFormViewSave({
  resource,
  id,
  isCreate,
  dataResource,
  modelMetadata,
  formFields,
  fieldByName,
  refineFields,
  defaultValues,
  onSaved,
  submit,
  createSubmit,
  defaultSlugSource,
  t,
}: UseFormViewSaveProps): FormViewSaveSurface {
  const refineResource = dataResource ? refineResourceName(dataResource) : "";
  const emptyValues = React.useMemo(
    () => emptyDraft(formFields, defaultValues),
    [defaultValues, formFields],
  );
  const createSeedNames = React.useMemo<ReadonlySet<string>>(
    () => new Set(Object.keys(defaultValues ?? {})),
    [defaultValues],
  );
  const [patchedRecord, setPatchedRecord] = React.useState<Row | null>(null);
  const baselineValuesRef = React.useRef<FormValues>(emptyValues);
  const manualSlugFieldsRef = React.useRef<Set<string>>(new Set());
  const [saveError, setSaveError] = React.useState<string | null>(null);
  const [serverFieldErrors, setServerFieldErrors] = React.useState<
    Record<string, readonly string[]>
  >({});
  const clearServerFieldError = React.useCallback((name: string) => {
    setServerFieldErrors((prev) => {
      if (!prev[name]) return prev;
      const { [name]: _removed, ...rest } = prev;
      return rest;
    });
  }, []);
  const requiredFieldNames = React.useMemo<ReadonlySet<string>>(() => {
    const required = new Set(modelMetadata?.rootFields?.requiredCreateFields ?? []);
    return new Set(
      formFields
        .filter((field) => required.has(field.name) && !field.readOnly)
        .map((field) => field.name),
    );
  }, [formFields, modelMetadata]);
  const writableFieldNames = React.useMemo<ReadonlySet<string> | null>(() => {
    const writable = isCreate
      ? modelMetadata?.rootFields?.createFields
      : submit
        ? undefined
        : modelMetadata
          ? formFields
              .map((field) => field.name)
              .filter((fieldName) => fieldUpdatable(modelMetadata, fieldName))
          : undefined;
    return writable ? new Set(writable) : null;
  }, [formFields, isCreate, modelMetadata, submit]);
  const form = useRefineForm<RowRecord, HttpError, FormValues>({
    defaultValues: baselineValuesRef.current,
    disableServerSideValidation: true,
    refineCoreProps: {
      action: isCreate ? "create" : "edit",
      resource: refineResource || "__angee_disabled__",
      id: isCreate ? undefined : (id as BaseKey | undefined),
      dataProviderName: dataResource?.schemaName,
      meta: { fields: refineFields },
      redirect: false,
      invalidates: isCreate ? ["list", "many"] : ["list", "many", "detail"],
      queryOptions: {
        enabled:
          !isCreate &&
          id !== null &&
          id !== undefined &&
          id !== "" &&
          dataResource !== null &&
          Boolean(dataResource.roots.detail),
      },
    },
  });
  const record = (form.refineCore.query?.data?.data as Row | undefined) ?? null;
  const displayRecord = patchedRecord ?? record;
  const loading = form.refineCore.query?.isFetching ?? false;
  // Refine may replace the query wrapper on a render. Keep panel/toolbar reload
  // identity stable while always calling the current query's refetch owner.
  const refetchRef = useLatestRef(form.refineCore.query?.refetch);
  const reload = React.useCallback(() => {
    void refetchRef.current?.();
  }, [refetchRef]);

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
  const linesActive =
    !isCreate &&
    linesConfig !== null &&
    linesField !== null &&
    (saveOperation.target !== null || Boolean(submit));
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
    await invalidate({
      resource: refineResourceName(dataResource),
      dataProviderName: dataResource.schemaName,
      id: id ?? undefined,
      invalidates: ["list", "many", "detail"],
    });
  }, [dataResource, id, invalidate]);

  const recordUnavailable = !isCreate && record == null;
  const submitOwner = submit ?? (isCreate ? createSubmit : undefined);
  const formReadOnly = React.useMemo(
    () =>
      recordUnavailable ||
      (!submitOwner &&
        !Boolean(isCreate ? dataResource?.roots.create : dataResource?.roots.update)) ||
      (formFields.length > 0 && formFields.every((field) => field.readOnly)),
    [dataResource, formFields, isCreate, recordUnavailable, submitOwner],
  );
  const formIsDirty = form.formState.isDirty;
  const pending = form.refineCore.mutation.isPending || form.formState.isSubmitting;
  const formIsDirtyRef = React.useRef(formIsDirty);
  React.useEffect(() => {
    formIsDirtyRef.current = formIsDirty;
  }, [formIsDirty]);
  const isDirtyNow = React.useCallback(() => formIsDirtyRef.current, []);
  useUnsavedChangesNavigationGuard({
    isDirty: formIsDirty,
    isDirtyNow,
    readOnly: formReadOnly,
  });

  // The form wrapper also churns; the seed effect depends on this stable reset
  // adapter and only re-runs when one of its record/field/line facts changes.
  const formResetRef = useLatestRef(form.reset);
  const resetForm = React.useCallback(
    (values: FormValues, options: { keepDirtyValues?: boolean } = {}) => {
      formResetRef.current(
        values,
        options.keepDirtyValues ? { keepDirtyValues: true } : undefined,
      );
      formIsDirtyRef.current = Boolean(
        options.keepDirtyValues && formIsDirtyRef.current,
      );
    },
    [formResetRef],
  );
  const runSubmit = React.useCallback(
    async (data: FormValues, lines: LineDiff | null = null): Promise<Row | null> => {
      if (submitOwner) {
        return (
          (await submitOwner(data, {
            resource,
            id: id ?? null,
            isCreate,
            record: displayRecord,
            lines,
          })) ?? null
        );
      }
      if (lines && lines.hasChanges && id != null && saveOperation.target !== null) {
        const saved = await resourceSave.save({
          pk: id,
          patch: data,
          lines: lines.payload,
        });
        if (saved) await invalidateResource();
        return saved;
      }
      const response = await form.refineCore.onFinish(data);
      return (response?.data ?? null) as Row | null;
    },
    [
      displayRecord,
      form.refineCore,
      id,
      invalidateResource,
      isCreate,
      resource,
      resourceSave,
      saveOperation.target,
      submitOwner,
    ],
  );
  const commitSavedRecord = React.useCallback(
    (saved: Row, options: { notify: boolean }): void => {
      const savedValues = recordToValues(
        saved,
        formFields,
        linesSeed(rowsFromRecord(saved)),
      );
      baselineValuesRef.current = savedValues;
      setPatchedRecord(saved);
      resetForm(savedValues);
      if (isCreate) manualSlugFieldsRef.current = new Set();
      if (options.notify) onSaved?.(saved);
    },
    [formFields, isCreate, linesSeed, onSaved, resetForm, rowsFromRecord],
  );
  const submitValues = React.useCallback(
    async (value: FormValues) => {
      setSaveError(null);
      setServerFieldErrors({});
      if (formReadOnly) {
        throw new Error(`Resource mutation for "${resource}" is disabled.`);
      }
      if (!dataResource) {
        throw new Error(`Resource metadata for "${resource}" is not available.`);
      }
      const data = mutationData(value, formFields, {
        dirtyFields: form.formState.dirtyFields as Record<string, unknown>,
        isCreate,
        seededFieldNames: createSeedNames,
        writableFields: writableFieldNames,
      });
      const linesDiff =
        linesActive && linesConfig && linesField
          ? diffLines(
              baselineLineRows(baselineValuesRef.current, linesField, seedLineRows),
              (value[linesField] as Row[] | undefined) ?? [],
              linesConfig,
            )
          : null;
      try {
        const saved = await runSubmit(data, linesDiff);
        if (saved) commitSavedRecord(saved, { notify: true });
      } catch (error) {
        const { fieldErrors, formErrors } = validationErrorsFromError(error);
        setServerFieldErrors(fieldErrors);
        setSaveError(
          formErrors.length > 0
            ? formErrors.join(" ")
            : Object.keys(fieldErrors).length > 0
              ? fieldValidationSummary(fieldErrors, fieldByName, t)
              : t("form.genericSaveError"),
        );
      }
    },
    [
      dataResource,
      fieldByName,
      formFields,
      formReadOnly,
      isCreate,
      commitSavedRecord,
      createSeedNames,
      linesActive,
      linesConfig,
      linesField,
      resource,
      runSubmit,
      seedLineRows,
      t,
      writableFieldNames,
      form.formState.dirtyFields,
    ],
  );
  const submitForm = form.handleSubmit(submitValues);
  const applyPatch = React.useCallback(
    async (patch: Record<string, unknown>): Promise<Row | null> => {
      if (id == null) throw new Error("No open record to update.");
      if (formReadOnly) {
        throw new Error(`Resource mutation for "${resource}" is disabled.`);
      }
      const saved = await runSubmit(patch);
      if (saved) {
        commitSavedRecord(saved, { notify: false });
        setSaveError(null);
        setServerFieldErrors({});
      }
      return saved;
    },
    [commitSavedRecord, formReadOnly, id, resource, runSubmit],
  );
  const patchRecord = React.useCallback(
    (patch: Record<string, unknown>): void => {
      const source = patchedRecord ?? record;
      if (!source) return;
      const next = { ...source, ...patch };
      const nextValues = recordToValues(
        next,
        formFields,
        linesSeed(rowsFromRecord(next)),
      );
      setPatchedRecord(next);
      baselineValuesRef.current = nextValues;
      resetForm(nextValues);
      setSaveError(null);
      setServerFieldErrors({});
    },
    [formFields, linesSeed, patchedRecord, record, resetForm, rowsFromRecord],
  );

  React.useEffect(() => {
    setPatchedRecord(null);
  }, [record]);
  React.useEffect(() => {
    setSaveError(null);
    setServerFieldErrors({});
  }, [resource, id]);
  const seededIdRef = React.useRef<string | null>(null);
  const seededRecordRef = React.useRef<Row | null>(null);
  React.useEffect(() => {
    if (isCreate) {
      if (seededIdRef.current !== null) {
        seededIdRef.current = null;
        seededRecordRef.current = null;
        baselineValuesRef.current = emptyValues;
        manualSlugFieldsRef.current = new Set();
        resetForm(emptyValues);
        setSaveError(null);
      }
      return;
    }
    const recordId = rowPublicId(record);
    if (!record || !recordId) return;
    const sameRecord = seededIdRef.current === recordId;
    const recordValues = recordToValues(
      record,
      formFields,
      linesSeed(seedLineRows),
    );
    const recordChanged = !sameRecord || seededRecordRef.current !== record;
    const cleanFieldShapeChanged =
      !recordChanged &&
      patchedRecord === null &&
      !formIsDirtyRef.current &&
      !formValuesEqual(recordValues, baselineValuesRef.current);
    if (!recordChanged && !cleanFieldShapeChanged) return;
    seededIdRef.current = recordId;
    seededRecordRef.current = record;
    const keepDirtyValues = sameRecord && formIsDirtyRef.current;
    baselineValuesRef.current = recordValues;
    resetForm(recordValues, { keepDirtyValues });
    setSaveError(null);
  }, [
    emptyValues,
    formFields,
    isCreate,
    linesSeed,
    patchedRecord,
    record,
    resetForm,
    seedLineRows,
  ]);

  const setValueRef = useLatestRef(form.setValue);
  const afterFieldChange = React.useCallback(
    (field: FieldDescriptor, value: unknown): void => {
      if (isCreate || !field.createOnly) {
        const seeds = field.prefill?.(value);
        if (seeds) {
          for (const [name, seed] of Object.entries(seeds)) {
            setValueRef.current(name, seed, {
              shouldDirty: true,
              shouldTouch: true,
            });
          }
        }
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
        setValueRef.current(slugField.name, slugify(value), {
          shouldDirty: true,
          shouldTouch: true,
        });
      }
    },
    [defaultSlugSource, formFields, isCreate, setValueRef],
  );
  const fieldReadOnly = React.useCallback(
    (field: FieldDescriptor): boolean =>
      recordUnavailable || Boolean(field.readOnly),
    [recordUnavailable],
  );
  const discardChanges = React.useCallback(() => {
    resetForm(baselineValuesRef.current);
  }, [resetForm]);

  return {
    form,
    displayRecord,
    loading,
    formReadOnly,
    formIsDirty,
    pending,
    saveError,
    serverFieldErrors,
    clearServerFieldError,
    requiredFieldNames,
    linesResource,
    linesField,
    linesActive,
    submitForm,
    discardChanges,
    applyPatch,
    patchRecord,
    reload,
    afterFieldChange,
    fieldReadOnly,
  };
}

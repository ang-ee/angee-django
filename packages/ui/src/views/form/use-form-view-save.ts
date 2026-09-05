import * as React from "react";
import {
  fieldUpdatable,
  refineResourceName,
  type DataResourceLinesMetadata,
  type DataResourceMetadata,
  type ModelMetadata,
  type Row,
} from "@angee/metadata";
import { useAngeeResourceSave } from "@angee/refine";
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
import { useForm, type UseFormReturn } from "react-hook-form";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import type { UiTranslate } from "../../i18n";
import { slugify } from "../../widgets";
import { fieldWidgetId, type FieldDescriptor } from "../page";
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
  missingRequiredFieldNames,
  mutationData,
  recordToValues,
  type FormValues,
  type LinesSeed,
} from "./form-view-model";
import { useSaveOperation } from "../resource/resource-operations";
import { validationErrorsFromError, serverErrorsFromForm } from "./validation-errors";
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

export type FormViewForm = UseFormReturn<FormValues>;

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

/** RHF owns values/baselines/errors; Refine owns resource reads and mutations. */
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
  const manualSlugFieldsRef = React.useRef<Set<string>>(new Set());
  const mounted = React.useRef(true);
  React.useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const submittingRef = React.useRef(false);
  const requiredFieldNames = React.useMemo<ReadonlySet<string>>(() => {
    if (!isCreate) return new Set();
    const required = new Set(modelMetadata?.rootFields?.requiredCreateFields ?? []);
    return new Set(
      formFields
        .filter((field) => required.has(field.name) && !field.readOnly)
        .map((field) => field.name),
    );
  }, [formFields, isCreate, modelMetadata]);
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
  const queryClient = useQueryClient();
  const { keys } = useKeys();
  const { identifier } = useResourceParams({ resource: refineResource || "__angee_disabled__" });
  const detailKey = React.useMemo(() => keys().data(dataResource?.schemaName ?? "default")
    .resource(identifier ?? "").action("one").id(id ?? "")
    .params({ fields: refineFields }).get(), [dataResource?.schemaName, id, identifier, keys, refineFields]);
  const read = useOne<RowRecord, HttpError>({
    resource: refineResource || "__angee_disabled__",
    id: id ?? undefined,
    dataProviderName: dataResource?.schemaName,
    meta: { fields: refineFields },
    queryOptions: {
      queryKey: detailKey,
      enabled: !isCreate && Boolean(id && dataResource?.roots.detail),
    },
  });
  const record = read.result ?? null;
  const displayRecord = record;
  const loading = read.query.isFetching;
  const reload = React.useCallback(() => { void read.query.refetch(); }, [read.query.refetch]);
  const create = useCreate<RowRecord, HttpError, FormValues>({
    resource: refineResource || "__angee_disabled__",
    dataProviderName: dataResource?.schemaName,
    meta: { fields: refineFields },
    invalidates: ["list", "many"],
    errorNotification: false,
  });
  const update = useUpdate<RowRecord, HttpError, FormValues>({
    resource: refineResource || "__angee_disabled__",
    dataProviderName: dataResource?.schemaName,
    meta: { fields: refineFields },
    invalidates: ["list", "many", "detail"],
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

  const values = React.useMemo(
    () => isCreate ? emptyValues : record
      ? recordToValues(record, formFields, linesSeed(seedLineRows)) : emptyValues,
    [emptyValues, formFields, isCreate, linesSeed, record, seedLineRows],
  );
  const form = useForm<FormValues>({
    defaultValues: emptyValues,
    values,
    resetOptions: { keepDirtyValues: true, keepDirty: true },
    shouldUnregister: false,
    resolver: (formValues) => {
      const missing = missingRequiredFieldNames(formValues, formFields, requiredFieldNames);
      return missing.length ? {
        values: {}, errors: Object.fromEntries(missing.map((name) => [name, { type: "required", message: t("form.required") }])),
      } : { values: formValues, errors: {} };
    },
  });
  const { reset, clearErrors, setError, setValue } = form;
  const serverFieldErrors = React.useMemo(() => serverErrorsFromForm(form.formState.errors), [form.formState.errors]);
  const saveError = form.formState.errors.root?.server?.message ?? null;
  const clearServerFieldError = React.useCallback((name: string) => clearErrors(name), [clearErrors]);
  const recordUnavailable = !isCreate && record == null;
  const submitOwner = submit ?? (isCreate ? createSubmit : undefined);
  const customSubmit = useMutation({
    mutationFn: async ({ data, lines }: { data: FormValues; lines: LineDiff | null }) =>
      (await submitOwner?.(data, { resource, id: id ?? null, isCreate, record: displayRecord, lines })) ?? null,
  });
  const formReadOnly = React.useMemo(
    () =>
      recordUnavailable ||
      (!submitOwner &&
        !Boolean(isCreate ? dataResource?.roots.create : dataResource?.roots.update)) ||
      (formFields.length > 0 && formFields.every((field) => field.readOnly)),
    [dataResource, formFields, isCreate, recordUnavailable, submitOwner],
  );
  const formIsDirty = form.formState.isDirty;
  const pending = create.mutation.isPending || update.mutation.isPending || customSubmit.isPending || resourceSave.fetching || form.formState.isSubmitting;
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

  const runSubmit = React.useCallback(
    async (data: FormValues, lines: LineDiff | null = null): Promise<Row | null> => {
      if (submitOwner) return customSubmit.mutateAsync({ data, lines });
      if (lines && lines.hasChanges && id != null && saveOperation.target !== null) {
        const saved = await resourceSave.save({
          pk: id,
          patch: data,
          lines: lines.payload,
        });
        if (saved) await invalidateResource();
        return saved;
      }
      const response = isCreate
        ? await create.mutateAsync({ values: data })
        : await update.mutateAsync({ id: id as BaseKey, values: data });
      return response?.data ?? null;
    },
    [
      customSubmit.mutateAsync,
      create.mutateAsync,
      update.mutateAsync,
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
    (saved: Row, options: {
      submitted?: FormValues;
      submittedFields?: readonly string[];
      notify?: boolean;
      refetchPartial?: boolean;
    } = {}): void => {
      // A mutation response is a patch over the latest cache, which may have
      // refreshed while the write was pending. Accepted submitted values fill
      // response omissions until canonical refetch; unsubmitted fields stay current.
      const submitted = Object.fromEntries((options.submittedFields ?? [])
        .map((name) => [name, options.submitted?.[name]]));
      const acceptedPatch = { ...submitted, ...saved };
      const accepted = isCreate ? acceptedPatch : queryClient.setQueryData<GetOneResponse<RowRecord>>(
        detailKey,
        (current) => ({ ...current, data: { ...(current?.data ?? record), ...acceptedPatch } }),
      )?.data ?? acceptedPatch;
      if (!mounted.current) return;
      const savedValues = recordToValues(accepted, formFields, linesSeed(rowsFromRecord(accepted)));
      reset(savedValues, { keepDirtyValues: false, keepDirty: false });
      formIsDirtyRef.current = false;
      if (isCreate) manualSlugFieldsRef.current.clear();
      if (options.notify) onSaved?.(accepted);
      // Fetch canonical server values when the response omitted any selected field.
      if (options.refetchPartial !== false && !isCreate && formFields.some((field) => !Object.hasOwn(saved, field.name))) reload();
    },
    [detailKey, formFields, isCreate, linesSeed, onSaved, queryClient, record, reload, reset, rowsFromRecord],
  );
  const submitValues = React.useCallback(
    async (value: FormValues) => {
      if (submittingRef.current) return;
      clearErrors();
      if (formReadOnly) {
        throw new Error(`Resource mutation for "${resource}" is disabled.`);
      }
      if (!dataResource) {
        throw new Error(`Resource metadata for "${resource}" is not available.`);
      }
      const data = mutationData(value, formFields, {
        dirtyFields: form.formState.dirtyFields as Record<string, unknown>,
        fieldMetadata: modelMetadata?.fields,
        isCreate,
        seededFieldNames: createSeedNames,
        writableFields: writableFieldNames,
      });
      const linesDiff =
        linesActive && linesConfig && linesField
          ? diffLines(
              baselineLineRows(form.formState.defaultValues ?? {}, linesField, seedLineRows),
              (value[linesField] as Row[] | undefined) ?? [],
              linesConfig,
            )
          : null;
      submittingRef.current = true;
      try {
        const saved = await runSubmit(data, linesDiff);
        if (saved) commitSavedRecord(saved, {
          submitted: value,
          submittedFields: [...Object.keys(data), ...(linesDiff?.hasChanges && linesField ? [linesField] : [])],
          notify: true,
        });
      } catch (error) {
        const { fieldErrors, formErrors } = validationErrorsFromError(error);
        if (!mounted.current) return;
        for (const [name, messages] of Object.entries(fieldErrors)) {
          setError(name, { type: "server", message: messages.join(" ") });
        }
        setError("root.server", { type: "server", message:
          formErrors.length > 0 ? formErrors.join(" ")
            : Object.keys(fieldErrors).length > 0
              ? fieldValidationSummary(fieldErrors, fieldByName, t)
              : t("form.genericSaveError"),
        });
      } finally {
        submittingRef.current = false;

      }
    },
    [
      clearErrors,
      setError,
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
      modelMetadata,
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
        commitSavedRecord(saved);
        clearErrors();
      }
      return saved;
    },
    [clearErrors, commitSavedRecord, formReadOnly, id, resource, runSubmit],
  );
  const patchRecord = React.useCallback((patch: Record<string, unknown>): void => {
    if (record) { commitSavedRecord(patch, { refetchPartial: false }); clearErrors(); }
  }, [clearErrors, commitSavedRecord, record]);

  const afterFieldChange = React.useCallback(
    (field: FieldDescriptor, value: unknown): void => {
      clearErrors(field.name);
      if (isCreate || !field.createOnly) {
        const seeds = field.prefill?.(value);
        if (seeds) {
          for (const [name, seed] of Object.entries(seeds)) {
            setValue(name, seed, {
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
        setValue(slugField.name, slugify(value), {
          shouldDirty: true,
          shouldTouch: true,
        });
      }
    },
    [clearErrors, defaultSlugSource, formFields, isCreate, setValue],
  );
  const fieldReadOnly = React.useCallback(
    (field: FieldDescriptor): boolean =>
      recordUnavailable || Boolean(field.readOnly),
    [recordUnavailable],
  );
  const discardChanges = React.useCallback(() => {
    reset(undefined, { keepDirtyValues: false, keepDirty: false });
    formIsDirtyRef.current = false;
  }, [reset]);

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

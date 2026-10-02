import * as React from "react";
import { useForm, type FieldValues, type DefaultValues, type Path, type Resolver } from "react-hook-form";

import { useToast } from "../../feedback";
import { useUiT } from "../../i18n";
import { useRuntimeViewAs } from "../../runtime";
import { useLatestRef } from "../../lib/use-latest-ref";
import { applyFormErrors, serverErrorsFromForm, formSubmitError, type FormSubmitResult } from "./validation-errors";
import { useFieldValidation, type FieldValidationForm } from "./use-field-validation";

/** RHF owns collection and validation; the callback owns the authored submission. */
export interface UseActionFormOptions<TValues extends FieldValues, TData = unknown, TSubmitValues extends FieldValues = TValues> {
  defaultValues?: DefaultValues<TValues>;
  resolver?: Resolver<TValues, unknown, TSubmitValues>;
  submit: (values: TSubmitValues) => FormSubmitResult<TData> | Promise<FormSubmitResult<TData>>;
  /** Called once after a successful submission, with its accepted data. */
  onSuccess?: (values: TSubmitValues, data: TData) => void;
  /** Toast the successful result's message. Default true. */
  toastSuccess?: boolean;
  /** Unclaimed issue paths also appear in the form-level summary. */
  fieldNames?: Iterable<string>;
  genericErrorMessage?: string;
}

export interface UseActionFormResult<TValues extends FieldValues, TSubmitValues extends FieldValues = TValues> {
  /** Pass to ActionFormProvider to bind controlled-editor draft validation. */
  form: FieldValidationForm<TValues, unknown, TSubmitValues>;
  /** Validate and submit the native form's current values. */
  run: () => Promise<boolean>;
  submitting: boolean;
  fieldErrors: Record<string, readonly string[]>;
  formError: string | null;
  saveConflict: boolean;
  clearFieldError: (name: string) => void;
  resetErrors: () => void;
}

/** One native form owns values, validation, errors, and the submission lifecycle. */
export function useActionForm<TValues extends FieldValues, TData = unknown, TSubmitValues extends FieldValues = TValues>(
  options: UseActionFormOptions<TValues, TData, TSubmitValues>,
): UseActionFormResult<TValues, TSubmitValues> {
  const t = useUiT();
  const toast = useToast();
  const { registerFieldValidation, validateFields } = useFieldValidation();
  const form = useForm<TValues, unknown, TSubmitValues>({ defaultValues: options.defaultValues, resolver: options.resolver });
  const { handleSubmit, clearErrors } = form;
  const optionsRef = useLatestRef(options);
  const preview = useLatestRef(useRuntimeViewAs());
  const submittingRef = React.useRef(false);
  const mounted = React.useRef(true);
  React.useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const run = React.useCallback(async (): Promise<boolean> => {
    if (submittingRef.current || preview.current.viewAs || preview.current.pending) return false;
    submittingRef.current = true;
    clearErrors();
    let succeeded = false;
    const validateEditors = () => validateFields(form.getValues(), (name, error) => form.setError(name as Path<TValues>, error));
    try {
      await handleSubmit(async (collected) => {
        if (preview.current.viewAs || preview.current.pending) return;
        if (validateEditors()) return;
        const { submit, onSuccess, toastSuccess = true, fieldNames, genericErrorMessage } = optionsRef.current;
        const fallback = genericErrorMessage ?? t("error.generic");
        const result = await Promise.resolve().then(() => submit(collected))
          .catch((cause) => formSubmitError(cause, fallback));
        if (!mounted.current) return;
        if (applyFormErrors(form, result, { fieldNames: fieldNames ?? [], fallback })) return;
        if (toastSuccess && result.message) toast.success({ title: result.message });
        succeeded = true;
        onSuccess?.(collected, result.data);
      }, () => { validateEditors(); })();
    } finally { submittingRef.current = false; }
    return succeeded;
  }, [clearErrors, form, handleSubmit, preview, t, toast, validateFields]);
  const clearFieldError = React.useCallback((name: string) => clearErrors(name as Path<TValues>), [clearErrors]);
  return {
    form: { ...form, registerFieldValidation }, run,
    submitting: form.formState.isSubmitting,
    fieldErrors: serverErrorsFromForm(form.formState.errors),
    formError: form.formState.errors.root?.server?.message ?? null,
    saveConflict: form.formState.errors.root?.server?.type === "conflict",
    clearFieldError,
    resetErrors: clearErrors,
  };
}

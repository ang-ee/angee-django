import * as React from "react";
import { useForm, type FieldValues, type DefaultValues, type Mode, type Path, type Resolver } from "react-hook-form";
import type { AuthoredMutateOptions } from "@angee/refine";

import { useToast } from "../../feedback";
import { useUiT } from "../../i18n";
import { useRuntimeViewAs } from "../../runtime";
import { useLatestRef } from "../../lib/use-latest-ref";
import {
  SURFACE_RENDERS_FAILURE, applyFormErrors, formErrorsFromForm, serverErrorsFromForm, formSubmitError, useRefusalFocus, type FormSubmitResult,
} from "./validation-errors";
import { useFieldValidation, type FieldValidationForm } from "./use-field-validation";
import { useLateAnswerReport, type LateAnswer } from "./late-answer";

/** RHF owns collection and validation; the callback owns the authored submission. */
export interface UseActionFormOptions<TValues extends FieldValues, TData = unknown, TSubmitValues extends FieldValues = TValues> {
  defaultValues?: DefaultValues<TValues>;
  resolver?: Resolver<TValues, unknown, TSubmitValues>;
  /**
   * React Hook Form's `mode`: when a value is checked before the first submit. Default `onSubmit`.
   * Give a value that stays the same while the form is open: a changed mode applies from the next
   * render, and a message shown under the old one stays, even once its value is corrected.
   */
  mode?: Mode;
  /**
   * Submit the collected values. The form renders a failure under its fields or in its
   * banner, so `options` turn refine's toast off: pass them on to the authored call.
   */
  submit: (values: TSubmitValues, options: AuthoredMutateOptions) => FormSubmitResult<TData> | Promise<FormSubmitResult<TData>>;
  /** Called once after a successful submission, with its accepted data. */
  onSuccess?: (values: TSubmitValues, data: TData) => void;
  /** Toast the successful result's message. Default true. */
  toastSuccess?: boolean;
  /**
   * The fields the form shows, in its order (`applyFormErrors`'s `fieldNames`): a refusal's reason
   * under any other path also shows in the form-level messages, as `path: reason`. Fields that
   * follow the values give a function, read with the form's values when the answer lands.
   */
  fieldNames?: Iterable<string> | ((values: TValues) => Iterable<string>);
  genericErrorMessage?: string;
  /** What a lost answer reads, for a keyed submit (`formSubmitError`'s `answerLost`). */
  answerLost?: string;
  /** The banner's line naming the refused fields (`applyFormErrors`'s `fieldSummary`). */
  fieldSummary?: (fieldErrors: Record<string, readonly string[]>) => string;
}

export interface UseActionFormResult<TValues extends FieldValues, TSubmitValues extends FieldValues = TValues> {
  /** Pass to ActionFormProvider to bind controlled-editor draft validation. */
  form: FieldValidationForm<TValues, unknown, TSubmitValues>;
  /** Validate and submit the native form's current values. */
  run: () => Promise<boolean>;
  submitting: boolean;
  fieldErrors: Record<string, readonly string[]>;
  /** The form-level messages of the last refused or failed submit, each its own entry; empty when there is none. */
  formErrors: readonly string[];
  /** Spread on the banner that renders `formErrors`: a refused submit brings the person to it or to its first refused field. */
  banner: ReturnType<typeof useRefusalFocus>["banner"];
  saveConflict: boolean;
  clearFieldError: (name: string) => void;
  resetErrors: () => void;
}

/** One native form owns values, validation, errors, and the submission lifecycle.
 * @owner ActionFormDialog
 */
export function useActionForm<TValues extends FieldValues, TData = unknown, TSubmitValues extends FieldValues = TValues>(
  options: UseActionFormOptions<TValues, TData, TSubmitValues>,
): UseActionFormResult<TValues, TSubmitValues> {
  const t = useUiT();
  const toast = useToast();
  const reportLate = useLateAnswerReport();
  const { registerFieldValidation, validateFields } = useFieldValidation();
  const form = useForm<TValues, unknown, TSubmitValues>({
    defaultValues: options.defaultValues, resolver: options.resolver, ...(options.mode ? { mode: options.mode } : {}),
  });
  const { handleSubmit, clearErrors } = form;
  // Publishers may retain this adapter. RHF updates formState on its stable
  // return object, so expose it live instead of capturing a render's proxy.
  const enhancedForm = React.useMemo(() => ({ ...form,
    get formState() { return form.formState; }, registerFieldValidation,
  }), [form, registerFieldValidation]);
  const optionsRef = useLatestRef(options);
  const preview = useLatestRef(useRuntimeViewAs());
  const submittingRef = React.useRef(false);
  const mounted = React.useRef(true);
  React.useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const formErrors = formErrorsFromForm(form.formState.errors);
  // Its fields lock while it submits: a refusal's focus waits until they are ready again.
  const { banner, focus } = useRefusalFocus(formErrors, { form, ready: !form.formState.isSubmitting });
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
        const { submit, onSuccess, toastSuccess = true, fieldNames = [], genericErrorMessage, answerLost, fieldSummary } = optionsRef.current;
        const fallback = genericErrorMessage ?? t("error.generic");
        let thrown: LateAnswer | undefined;
        const result = await Promise.resolve().then(() => submit(collected, SURFACE_RENDERS_FAILURE))
          .catch((cause) => { thrown = { thrown: cause }; return formSubmitError(cause, fallback, answerLost); });
        if (!mounted.current) {
          // The form went away while the write ran: its answer is still reported, so nobody
          // writes it again, or trusts it went out, unaware.
          if (thrown) reportLate(thrown, fallback);
          else if (result.status !== "ok" || toastSuccess) reportLate(result, fallback);
          return;
        }
        // A refusal brings the person to it: the banner, which then also names the refused fields, or
        // the first refused field (`applyFormErrors` decides).
        const shown = typeof fieldNames === "function" ? fieldNames(form.getValues()) : fieldNames;
        if (applyFormErrors(form, result, { fieldNames: shown, fallback, focus, ...(fieldSummary ? { fieldSummary } : {}) })) return;
        if (toastSuccess && result.message) toast.success({ title: result.message });
        succeeded = true;
        onSuccess?.(collected, result.data);
      }, () => { validateEditors(); })();
    } finally { submittingRef.current = false; }
    return succeeded;
  }, [clearErrors, focus, form, handleSubmit, preview, reportLate, t, toast, validateFields]);
  const clearFieldError = React.useCallback((name: string) => clearErrors(name as Path<TValues>), [clearErrors]);
  return {
    form: enhancedForm, run,
    submitting: form.formState.isSubmitting,
    fieldErrors: serverErrorsFromForm(form.formState.errors),
    formErrors,
    banner,
    saveConflict: form.formState.errors.root?.server?.type === "conflict",
    clearFieldError,
    resetErrors: clearErrors,
  };
}

import * as React from "react";
import { useForm, type FieldValues, type DefaultValues, type UseFormReturn, type Path } from "react-hook-form";
import { serverErrorsFromForm } from "./validation-errors";
import type { ActionOutcome } from "@angee/refine";

import { errorMessage, useToast } from "../../feedback";
import { useUiT } from "../../i18n";

/**
 * Options for {@link useActionForm}. The consumer owns how values are collected
 * (react-hook-form args, local field state, …); the hook owns everything after
 * collection — firing the action, reading its {@link ActionOutcome}, binding the
 * in-band `validationErrors`, and the toast/close on `ok`.
 */
export interface UseActionFormOptions<TValues extends FieldValues> {
  /** Initial values when this hook also owns the rendered collecting form. */
  defaultValues?: DefaultValues<TValues>;
  /**
   * Fire the collected values and return the action's in-band `ActionOutcome`.
   * A thrown (non-domain / GraphQL) failure is caught and surfaced as `formError`;
   * an `ok=false` outcome binds its `validationErrors` and stays open. A `null`
   * outcome (the shape `extractActionOutcome` returns when the response carries no
   * in-band envelope) is treated as a form-level failure here, so a consumer
   * passing `extractActionOutcome(...)` through never has to coalesce its own
   * `FAILED_OUTCOME` constant. Returning a non-null `ActionOutcome` stays valid.
   */
  submit: (values: TValues) => ActionOutcome | null | Promise<ActionOutcome | null>;
  /**
   * Called once after an `ok=true` outcome (after the success toast) — e.g. reset
   * the fields, reload the record, or close the dialog. The submitted values and
   * the outcome are passed through.
   */
  onSuccess?: (values: TValues, outcome: ActionOutcome) => void;
  /** Toast the outcome `message` on success. Default `true`; opt out for an inline surface. */
  toastSuccess?: boolean;
  /**
   * The field/arg names the collecting form binds errors to. A `validationErrors`
   * key outside this set is folded into the form-level `formError` instead of
   * being dropped. Omit when the caller binds nothing (all errors are form-level).
   */
  fieldNames?: Iterable<string>;
  /** Fallback form-level message when the outcome/exception carries none. */
  genericErrorMessage?: string;
}

/**
 * The lifecycle state {@link useActionForm} owns for the collecting form to bind:
 * the busy flag, the per-field server errors, the form-level error, and the
 * helpers to fire and to clear a bound field error.
 */
export interface UseActionFormResult<TValues extends FieldValues> {
  /** Native collecting form; values, submission and errors share this owner. */
  form: UseFormReturn<TValues>;
  /** Fire the collected values through the action lifecycle; resolves to the `ok` flag. */
  run: (values: TValues) => Promise<boolean>;
  /** True while the action is in flight — gates the submit control and re-entry. */
  submitting: boolean;
  /** Per-field messages bound from the outcome's in-band `validationErrors`. */
  fieldErrors: Record<string, readonly string[]>;
  /** The form-level message (non-field validation, a thrown failure, or the fallback). */
  formError: string | null;
  /** Clear one field's bound error — call it as the user edits that field. */
  clearFieldError: (name: string) => void;
  /** Clear the form-level and per-field errors (e.g. when reopening the form). */
  resetErrors: () => void;
}

/** Decode action outcomes into the collecting RHF form's native lifecycle. */
export function useActionForm<TValues extends FieldValues>(
  options: UseActionFormOptions<TValues>,
): UseActionFormResult<TValues> {
  const t = useUiT();
  const toast = useToast();
  const form = useForm<TValues>({ defaultValues: options.defaultValues });
  const { reset, handleSubmit, setError, clearErrors } = form;
  const optionsRef = React.useRef(options);
  optionsRef.current = options;
  const submittingRef = React.useRef(false);
  const mounted = React.useRef(true);
  React.useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const run = React.useCallback(async (values: TValues): Promise<boolean> => {
    if (submittingRef.current) return false;
    submittingRef.current = true;
    reset(values, { keepDefaultValues: true });
    let succeeded = false;
    try {
      await handleSubmit(async (collected) => {
        const { submit, onSuccess, toastSuccess = true, fieldNames, genericErrorMessage } = optionsRef.current;
        const fallback = genericErrorMessage ?? t("error.generic");
        try {
          const outcome = await submit(collected);
          if (!mounted.current) return;
          if (outcome?.ok) {
            if (toastSuccess && outcome.message) toast.success({ title: outcome.message });
            succeeded = true;
            onSuccess?.(collected, outcome);
            return;
          }
          for (const [name, messages] of Object.entries(outcome?.validationErrors ?? {})) {
            setError(name as Path<TValues>, { type: "server", message: messages.join(" "), types: { server: [...messages] } });
          }
          setError("root.server", { type: "server", message: outcome ? formLevelMessage(outcome, new Set(fieldNames)) ?? fallback : fallback });
        } catch (cause) {
          if (mounted.current) setError("root.server", { type: "server", message: errorMessage(cause, fallback) });
        }
      })();
    } finally { submittingRef.current = false; }
    return succeeded;
  }, [handleSubmit, reset, setError, t, toast]);
  const clearFieldError = React.useCallback((name: string) => clearErrors(name as Path<TValues>), [clearErrors]);
  return {
    form, run,
    submitting: form.formState.isSubmitting,
    fieldErrors: serverErrorsFromForm(form.formState.errors),
    formError: form.formState.errors.root?.server?.message ?? null,
    clearFieldError,
    resetErrors: clearErrors,
  };
}

/**
 * A form-level failure summary: the outcome message plus any `validationErrors`
 * keys that match no bound field name — so a non-field error is surfaced rather
 * than silently dropped.
 */
export function formLevelMessage(
  outcome: ActionOutcome,
  fieldNames: ReadonlySet<string>,
): string | null {
  const unmatched = Object.entries(outcome.validationErrors ?? {})
    .filter(([field]) => !fieldNames.has(field))
    .flatMap(([, messages]) => messages);
  const parts = [outcome.message, ...unmatched].filter((part): part is string =>
    Boolean(part),
  );
  return parts.length > 0 ? parts.join(" ") : null;
}

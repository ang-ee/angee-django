import * as React from "react";
import type { ActionOutcome } from "@angee/refine";

import { errorMessage, useToast } from "../feedback";
import { useUiT } from "../i18n";

/**
 * Options for {@link useActionForm}. The consumer owns how values are collected
 * (react-hook-form args, local field state, …); the hook owns everything after
 * collection — firing the action, reading its {@link ActionOutcome}, binding the
 * in-band `validationErrors`, and the toast/close on `ok`.
 */
export interface UseActionFormOptions<TValues> {
  /**
   * Fire the collected values and return the action's in-band `ActionOutcome`.
   * A thrown (non-domain / GraphQL) failure is caught and surfaced as `formError`;
   * an `ok=false` outcome binds its `validationErrors` and stays open.
   */
  submit: (values: TValues) => ActionOutcome | Promise<ActionOutcome>;
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
export interface UseActionFormResult<TValues> {
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

const EMPTY_ERRORS: Record<string, readonly string[]> = {};

/**
 * The typed-args action-form lifecycle owner: fire the collected values, read the
 * backend `ActionOutcome`, bind its in-band `validationErrors` to the form's
 * fields, surface a form-level message for non-field/thrown failures, toast and
 * hand off on `ok`, and track the in-flight flag — the boilerplate every
 * authored-mutation dialog (register-payment, unreconcile, the record-activity
 * scheduler, …) otherwise re-derives. The consumer composes it with its own value
 * collection (react-hook-form or local state) and renders `fieldErrors`/`formError`.
 */
export function useActionForm<TValues>(
  options: UseActionFormOptions<TValues>,
): UseActionFormResult<TValues> {
  const t = useUiT();
  const toast = useToast();
  const [submitting, setSubmitting] = React.useState(false);
  const [fieldErrors, setFieldErrors] =
    React.useState<Record<string, readonly string[]>>(EMPTY_ERRORS);
  const [formError, setFormError] = React.useState<string | null>(null);

  // Read the latest options at call time (a stable `run`), and guard re-entry off
  // a ref so an in-flight submit is not re-fired while the state update is pending.
  const optionsRef = React.useRef(options);
  optionsRef.current = options;
  const submittingRef = React.useRef(false);

  const clearFieldError = React.useCallback((name: string) => {
    setFieldErrors((current) => {
      if (!current[name]) return current;
      const { [name]: _removed, ...rest } = current;
      return rest;
    });
  }, []);

  const resetErrors = React.useCallback(() => {
    setFieldErrors(EMPTY_ERRORS);
    setFormError(null);
  }, []);

  const run = React.useCallback(
    async (values: TValues): Promise<boolean> => {
      if (submittingRef.current) return false;
      const {
        submit,
        onSuccess,
        toastSuccess = true,
        fieldNames,
        genericErrorMessage,
      } = optionsRef.current;
      const fallback = genericErrorMessage ?? t("error.generic");
      submittingRef.current = true;
      setSubmitting(true);
      setFormError(null);
      try {
        const outcome = await submit(values);
        if (outcome.ok) {
          setFieldErrors(EMPTY_ERRORS);
          if (toastSuccess && outcome.message) {
            toast.success({ title: outcome.message });
          }
          onSuccess?.(values, outcome);
          return true;
        }
        setFieldErrors(outcome.validationErrors ?? EMPTY_ERRORS);
        setFormError(formLevelMessage(outcome, new Set(fieldNames)) ?? fallback);
        return false;
      } catch (cause) {
        setFieldErrors(EMPTY_ERRORS);
        setFormError(errorMessage(cause, fallback));
        return false;
      } finally {
        submittingRef.current = false;
        setSubmitting(false);
      }
    },
    [t, toast],
  );

  return { run, submitting, fieldErrors, formError, clearFieldError, resetErrors };
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

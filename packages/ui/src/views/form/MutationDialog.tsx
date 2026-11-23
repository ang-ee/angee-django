import * as React from "react";
import { set, useForm, useWatch, type FieldErrors } from "react-hook-form";
import { stringValue as wireStringValue, type AuthoredMutateOptions } from "@angee/refine";

import { DialogForm } from "../../fragments/DialogForm";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { Button } from "../../ui/button";
import type { DialogPlacement, DialogSize } from "../../ui/dialog";
import { useToast } from "../../feedback";
import { useUiT, type UiTranslate } from "../../i18n";
import { dateFromUnknown, formatDateTimeStorage } from "../../widgets/date-format";
import {
  REQUIRED_RULE, emptyValueForField, isStructuredPresenceField, structuredFieldErrors, type FieldRuleError,
} from "./field-values";
import { isFieldControlVisible, visibleFields } from "./form-view-model";
import { DescriptorFieldList, type DescriptorField } from "./DescriptorFieldList";
import {
  SURFACE_RENDERS_FAILURE, applyFormErrors, fieldErrorsFromRules, formSubmitError, invalidFormSubmit,
  savedFormSubmitResult, type FormSubmitResult, type ValidationErrors,
} from "./validation-errors";
import { useFieldValidation } from "./use-field-validation";
import { ActionFormProvider } from "./ActionFormProvider";
import { useRuntimeViewAs } from "../../runtime";
import { useLatestRef } from "../../lib/use-latest-ref";

export {
  LabeledDescriptorField,
} from "./DescriptorFieldList";
export { emptyValueForField } from "./field-values";

/** Raw values held by dialog controls before the authored mutation boundary. */
export type MutationDialogValues = Readonly<Record<string, unknown>>;

/** Decode dialog-control values into one mutation's typed variable shape. */
export type MutationDialogParseValues<TValues> = (
  values: MutationDialogValues,
) => TValues;

/**
 * Shared scalar codecs for {@link MutationDialogParseValues} implementations.
 * Text follows the GraphQL wire boundary: actual string values are trimmed and
 * empty text is `null`, unlike the old vendor-local coercers that silently
 * produced `""`. `requiredString` and `verbatimString` are invariant guards for
 * developer-authored required fields, so their errors deliberately name the
 * declaration field rather than presenting translated user validation copy.
 * `verbatimString` is the explicit exception for whitespace-sensitive secrets;
 * `integer` takes a label-aware translated formatter because malformed numeric
 * input is reachable user validation. `datetime` is the action-argument wire
 * boundary: it writes the picked moment as the `datetime` widget stores it
 * (`formatDateTimeStorage`: the local wall time with its UTC offset), so Django
 * never receives a naive datetime.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export const mutationDialogValueCodecs = {
  string(value: unknown): string | null {
    return typeof value === "string" ? wireStringValue(value) : null;
  },
  requiredString(value: unknown, fieldName: string): string {
    const parsed = typeof value === "string" ? wireStringValue(value) : null;
    if (parsed === null) {
      throw new TypeError(
        `MutationDialog invariant: required field "${fieldName}" did not contain a non-empty string.`,
      );
    }
    return parsed;
  },
  verbatimString(value: unknown, fieldName: string): string {
    if (typeof value !== "string" || value === "") {
      throw new TypeError(
        `MutationDialog invariant: required verbatim field "${fieldName}" did not contain a non-empty string.`,
      );
    }
    return value;
  },
  integer(
    value: unknown,
    fieldLabel: string,
    invalidMessage: (fieldLabel: string) => string,
  ): number | null {
    if (value == null || (typeof value === "string" && value.trim() === "")) {
      return null;
    }
    const parsed =
      typeof value === "number"
        ? value
        : typeof value === "string"
          ? Number(value)
          : Number.NaN;
    if (!Number.isInteger(parsed)) {
      throw new TypeError(invalidMessage(fieldLabel));
    }
    return parsed;
  },
  datetime(value: unknown): string | null {
    if (value == null || (typeof value === "string" && value.trim() === "")) {
      return null;
    }
    const parsed = dateFromUnknown(value);
    if (parsed === null) {
      throw new TypeError(
        "MutationDialog invariant: datetime value was not a valid ISO-8601 date-time.",
      );
    }
    return formatDateTimeStorage(parsed);
  },
} as const;

/**
 * The one submission of a {@link MutationDialog}: any `onSubmit`, or an authored `mutation` the dialog runs.
 * Either is called with the {@link AuthoredMutateOptions} that turn refine's generic error
 * notification off, to pass on to the authored call it makes: the dialog renders the failure.
 */
export type MutationDialogSubmission<TValues, TResult, TData = TResult> =
  | {
      /**
       * Submit the parsed values through any owner (a store binding, an action
       * mutation), passing `options` on to the authored call it makes so the
       * failure the dialog renders raises no toast besides.
       */
      onSubmit: (
        values: TValues,
        options?: AuthoredMutateOptions,
      ) => FormSubmitResult<TResult> | Promise<FormSubmitResult<TResult>>;
      mutation?: never;
      mutationResult?: never;
    }
  | {
      /**
       * The authored mutation the dialog runs with the parsed values, as
       * `useAuthoredMutation` (or `useAuthoredResourceMutation`) returns it. The dialog
       * renders its refusal itself, under the field it names or else in the dialog, so
       * the call turns refine's generic error notification off: a failure is reported once.
       */
      mutation: (variables: TValues, options: AuthoredMutateOptions) => Promise<TData | undefined>;
      /** Read the mutation's data as the submit result; by default the data is the result. */
      mutationResult?: (data: TData) => FormSubmitResult<TResult>;
      onSubmit?: never;
    };

export type MutationDialogProps<
  TValues extends Record<string, unknown>,
  TResult = unknown,
  TData = TResult,
> = MutationDialogBaseProps<TValues, TResult> & MutationDialogSubmission<TValues, TResult, TData>;

interface MutationDialogBaseProps<
  TValues extends Record<string, unknown>,
  TResult,
> {
  /** Controlled visibility. Omit with `trigger` to let the dialog own it. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Native trigger; when supplied, the dialog owns open state and focus pairing. */
  trigger?: React.ReactElement;
  title: React.ReactNode;
  description?: React.ReactNode;
  fields: readonly DescriptorField[];
  /** Seeds raw control state; parsed mutation variables may use different keys. */
  initialValues?: Readonly<Record<string, unknown>>;
  submitLabel: React.ReactNode;
  submittingLabel?: React.ReactNode;
  cancelLabel?: React.ReactNode;
  errorFallback?: string;
  /** Decode raw control state before it crosses the authored-mutation boundary. */
  parseValues: MutationDialogParseValues<TValues>;
  /**
   * Optional authoritative validation step that can bind errors to declared fields.
   * It gets the submission's {@link AuthoredMutateOptions} for an authored call it makes.
   */
  validate?: (
    values: TValues,
    options?: AuthoredMutateOptions,
  ) => ValidationErrors | null | undefined | Promise<ValidationErrors | null | undefined>;
  onSubmitted?: (result: TResult, values: TValues) => void;
  closeOnSubmit?: boolean;
  /** Additional domain readiness gate evaluated from the current raw form values. */
  canSubmit?: (values: Readonly<Record<string, unknown>>) => boolean;
  size?: DialogSize;
  placement?: DialogPlacement;
}

/**
 * FieldDescriptor-driven mutation dialog for addon toolbar actions. It owns the
 * copied dialog ceremony: reset-on-close, value state, required gating,
 * submit busy/error state, and rendering descriptor fields through the shared
 * widget registry. It reports a failure once: under the field a refusal names,
 * else in the dialog. Every call it makes for its caller (`validate`, `onSubmit`,
 * `mutation`) gets the `AuthoredMutateOptions` that keep refine from toasting it besides.
 * @kind surface
 */
export function MutationDialog<
  TValues extends Record<string, unknown>,
  TResult = unknown,
  TData = TResult,
>(props: MutationDialogProps<TValues, TResult, TData>): React.ReactElement | null {
  const [open, setOpen] = React.useState(false);
  const controlled = props.open !== undefined;
  const visible = props.open ?? open;
  const onOpenChange = React.useCallback((next: boolean) => {
    if (!controlled) setOpen(next);
    props.onOpenChange?.(next);
  }, [controlled, props.onOpenChange]);
  if (!props.trigger && !visible) return null;
  return <MutationDialogInstance {...props} open={visible} onOpenChange={onOpenChange} />;
}

/**
 * Run a dialog's one submission, its `onSubmit` or its authored `mutation`, with the
 * generic error notification off (the dialog renders the refusal). The submission's
 * result is awaited, so every outcome, a throw included, settles the returned promise.
 * A mutation that resolves no data does not acknowledge the dialog: it fails with `fallback`.
 * @kind null
 * @reason Runtime infrastructure: the dialog's submission contract, shared with its test double.
 */
export async function submitMutationDialog<TValues, TResult, TData>(
  submission: MutationDialogSubmission<TValues, TResult, TData>,
  values: TValues,
  fallback: string,
): Promise<FormSubmitResult<TResult>> {
  if (!submission.mutation) return submission.onSubmit(values, SURFACE_RENDERS_FAILURE);
  const { mutation, mutationResult } = submission;
  const data = await mutation(values, SURFACE_RENDERS_FAILURE);
  if (data == null) return savedFormSubmitResult<TResult>(null, fallback);
  return mutationResult ? mutationResult(data) : { status: "ok", data: data as unknown as TResult };
}

function MutationDialogInstance<TValues extends Record<string, unknown>, TResult, TData>({
  open,
  onOpenChange,
  title,
  description,
  fields,
  initialValues,
  submitLabel,
  submittingLabel,
  cancelLabel,
  errorFallback,
  parseValues,
  validate,
  onSubmit,
  mutation,
  mutationResult,
  onSubmitted,
  closeOnSubmit = true,
  canSubmit,
  size = "md",
  placement = "prompt",
  trigger,
}: Omit<MutationDialogBaseProps<TValues, TResult>, "open" | "onOpenChange">
  & MutationDialogSubmission<TValues, TResult, TData> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  const t = useUiT();
  const toast = useToast();
  const preview = useRuntimeViewAs();
  const previewBlocked = Boolean(preview.viewAs || preview.pending);
  const previewBlockedRef = useLatestRef(previewBlocked);
  const { registerFieldValidation, validateFields } = useFieldValidation();
  const form = useForm<Record<string, unknown>>({
    defaultValues: initialDialogValues(fields, initialValues),
    mode: "onChange",
    resolver: (formValues) => {
      const errors = dialogRuleErrors(fields, formValues, t);
      validateFields(formValues, (name, error) => set(errors, name, error));
      return Object.keys(errors).length ? { values: {}, errors } : { values: formValues, errors: {} };
    },
  });
  const session = React.useRef(0);
  const submittingRef = React.useRef(false);
  const previousOpen = React.useRef(open);
  React.useEffect(() => {
    if (previousOpen.current !== open) {
      previousOpen.current = open;
      session.current += 1;
      submittingRef.current = false;
      form.reset(initialDialogValues(fields, initialValues));
      form.clearErrors();
    }
  }, [fields, form, initialValues, open]);
  const values = useWatch({ control: form.control });
  // Resolving lays `resolve`'s descriptor over each declared one, so its DescriptorField members stay.
  const shownFields = visibleFields(fields, values) as DescriptorField[];
  const renderedFieldNames = shownFields.filter((field) => !field.hidden).map((field) => field.name);
  const submitting = form.formState.isSubmitting;
  const error = form.formState.errors.root?.server?.message ?? null;
  const fieldsReady = form.formState.isValid || (!form.formState.isDirty
    && shownFields
      .filter((field) => !field.readOnly && !field.readOnlyWhen?.(values))
      .every((field) => !field.required && !field.presenceRequired));
  const ready = fieldsReady && (canSubmit?.(values) ?? true);
  const mounted = React.useRef(true);
  React.useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const footer = (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabled={submitting}
        onClick={() => onOpenChange(false)}
      >
        {cancelLabel ?? t("dialog.cancel")}
      </Button>
      <Button
        type="submit"
        variant="primary"
        size="sm"
        disabled={!ready || submitting || previewBlocked}
        loading={submitting}
        loadingText={submittingLabel ?? submitLabel}
      >
        {submitLabel}
      </Button>
    </>
  );

  const errorOptions = { fieldNames: renderedFieldNames, fallback: errorFallback ?? t("error.generic") };
  const allowRootErrorRetry = async (): Promise<void> => {
    if (!renderedFieldNames.some((name) => form.getFieldState(name).invalid)) {
      await form.trigger(shownFields.map((field) => field.name));
    }
  };

  const submitReady = form.handleSubmit(async (collected) => {
    if (submittingRef.current || previewBlockedRef.current) return;
    submittingRef.current = true;
    const submittedSession = session.current;
    form.clearErrors();
    let accepted: { result: Extract<FormSubmitResult<TResult>, { status: "ok" }>; values: TValues } | undefined;
    try {
      const submittedValues = parseValues(collected);
      const validation = await validate?.(submittedValues, SURFACE_RENDERS_FAILURE);
      if (!mounted.current || session.current !== submittedSession) return;
      if (validation) {
        applyFormErrors(form, invalidFormSubmit(validation), errorOptions);
        await allowRootErrorRetry();
        return;
      }
      const submission = (mutation ? { mutation, mutationResult } : { onSubmit }) as MutationDialogSubmission<TValues, TResult, TData>;
      const result = await submitMutationDialog(submission, submittedValues, errorOptions.fallback);
      if (!mounted.current || session.current !== submittedSession) return;
      if (applyFormErrors(form, result, errorOptions)) {
        await allowRootErrorRetry();
        return;
      }
      accepted = { result, values: submittedValues };
    } catch (cause) {
      if (mounted.current && session.current === submittedSession) {
        applyFormErrors(form, formSubmitError(cause, errorOptions.fallback), errorOptions);
        await allowRootErrorRetry();
      }
    } finally {
      if (session.current === submittedSession) submittingRef.current = false;
    }
    if (accepted) {
      if (accepted.result.message) toast.success({ title: accepted.result.message });
      if (closeOnSubmit) onOpenChange(false);
      onSubmitted?.(accepted.result.data, accepted.values);
    }
  });
  const submit = (event: React.FormEvent<HTMLFormElement>): void => {
    if (!ready) {
      event.preventDefault();
      return;
    }
    void submitReady(event);
  };

  return (
    <DialogForm
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={description}
      footer={footer}
      onSubmit={submit}
      size={size}
      placement={placement}
      trigger={trigger}
    >
      <ActionFormProvider {...{ ...form, registerFieldValidation }}>
        <DescriptorFieldList fields={fields} unregisterHidden />
      </ActionFormProvider>
      <ErrorBanner description={error} />
    </DialogForm>
  );
}

/** A dialog's errors for the rules its fields fail. A structured field, one with a presence flag,
 * a template or a bound (`isStructuredPresenceField`), answers to presence, nullability and, with
 * `validateConstraints`, its constraints (a number's type, its bounds), and its `required` flag
 * is not read; any other field fails `required` when it is required and empty. It shares with
 * the record form's check (`formRuleErrors`) how a structured field's failed rule is found
 * (`structuredFieldErrors`) and how a failed rule becomes an error with its message
 * (`fieldErrorsFromRules`). They differ in which fields they ask and how they read values: this
 * one asks only the fields shown and editable for `values`, checks a structured field's
 * constraints only with `validateConstraints` (a schema-resolved form leaves them and their
 * messages to that resolver), and reads each name as one flat key, where an own key holding
 * `undefined` is present unless the field is omittable.
 * @kind null
 * @reason Descriptor validation shared by form surfaces.
 */
export function dialogRuleErrors(
  fields: readonly DescriptorField[] | ((values: Record<string, unknown>) => readonly DescriptorField[]),
  values: Record<string, unknown>,
  t: UiTranslate,
  validateConstraints = true,
): FieldErrors<Record<string, unknown>> {
  // Resolving lays `resolve`'s descriptor over each declared one, so its DescriptorField members stay.
  const resolved = visibleFields(typeof fields === "function" ? fields(values) : fields, values) as DescriptorField[];
  const editable = resolved.filter((field) => isFieldControlVisible(field, values) && !field.readOnly && !field.readOnlyWhen?.(values));
  return fieldErrorsFromRules(editable.flatMap((field): readonly FieldRuleError[] => {
    if (isStructuredPresenceField(field)) {
      const value = values[field.name];
      const present = Object.hasOwn(values, field.name) && !(field.omittable && value === undefined);
      return structuredFieldErrors(field, value, present, field.name, validateConstraints);
    }
    return field.required && emptyDialogValue(values[field.name])
      ? [{ path: field.name, ...REQUIRED_RULE }]
      : [];
  }), t);
}

function initialDialogValues(
  fields: readonly DescriptorField[],
  initialValues: Readonly<Record<string, unknown>> | undefined,
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const field of fields) {
    if (initialValues && Object.hasOwn(initialValues, field.name)) {
      values[field.name] = initialValues[field.name];
    } else if (field.hasDefault) {
      values[field.name] = field.defaultValue;
    } else if (!field.omittable && !field.presenceRequired) {
      values[field.name] = field.nullable ? null : emptyValueForField(field);
    }
  }
  return values;
}

/** Whether a dialog value counts as unfilled for the required-submit gate. */
export function emptyDialogValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

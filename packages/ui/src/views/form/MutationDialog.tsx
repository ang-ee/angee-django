import * as React from "react";
import { set, useForm, useWatch, type FieldErrors } from "react-hook-form";
import { stringValue as wireStringValue, type AuthoredMutateOptions } from "@angee/refine";

import { DialogForm, type DialogFormFooterContext } from "../../fragments/DialogForm";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { Button } from "../../ui/button";
import type { DialogPlacement, DialogSize } from "../../ui/dialog";
import { useToast } from "../../feedback";
import { useUiT, type UiTranslate } from "../../i18n";
import { dateFromUnknown, formatDateTimeStorage } from "../../widgets/date-format";
import { emptyValueForField, fieldRuleErrors, refusesSeed } from "./field-values";
import { isFieldControlVisible, visibleFields } from "./form-view-model";
import { DescriptorFieldList, type DescriptorField } from "./DescriptorFieldList";
import {
  SURFACE_RENDERS_FAILURE, applyFormErrors, fieldErrorsFromRules, formErrorsFromForm, formSubmitError, invalidFormSubmit,
  markMemberErrors, savedFormSubmitResult, type FormSubmitResult, type ValidationErrors,
} from "./validation-errors";
import { useFieldValidation } from "./use-field-validation";
import { useLateAnswerReport } from "./late-answer";
import { ActionFormProvider } from "./ActionFormProvider";
import { useRuntimeViewAs } from "../../runtime";
import { useLatestRef } from "../../lib/use-latest-ref";
import { useIdempotencyDraftOwner, useIdempotencyKeys } from "../../lib/idempotency-key";

export {
  LabeledDescriptorField,
} from "./DescriptorFieldList";

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

/** The idempotency key a keyed submission is called with last: none, or the draft's. */
type SubmissionKey<TKeyed extends boolean> = TKeyed extends true ? [idempotencyKey: string] : [];
/** What `onSubmit` is called with: the values, the quiet call options, and a keyed one's key. */
type SubmitArguments<TValues, TKeyed extends boolean> = TKeyed extends true
  ? [values: TValues, options: AuthoredMutateOptions, idempotencyKey: string]
  : [values: TValues, options?: AuthoredMutateOptions];

/**
 * The one submission of a {@link MutationDialog}: any `onSubmit`, or an authored `mutation` the dialog runs.
 * Either is called with the {@link AuthoredMutateOptions} that turn refine's generic error
 * notification off, to pass on to the authored call it makes: the dialog renders the failure.
 * A keyed one (`TKeyed`, a dialog given `idempotencyScope`) is also called with its draft's
 * idempotency key.
 */
export type MutationDialogSubmission<TValues, TResult, TData = TResult, TKeyed extends boolean = false> =
  | {
      /**
       * Submit the parsed values through any owner (a store binding, an action
       * mutation), passing `options` on to the authored call it makes so the
       * failure the dialog renders raises no toast besides.
       */
      onSubmit: (...args: SubmitArguments<TValues, TKeyed>) => FormSubmitResult<TResult> | Promise<FormSubmitResult<TResult>>;
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
      mutation: (variables: TValues, options: AuthoredMutateOptions, ...key: SubmissionKey<TKeyed>) => Promise<TData | undefined>;
      /** Read the mutation's data as the submit result; by default the data is the result. */
      mutationResult?: (data: TData) => FormSubmitResult<TResult>;
      onSubmit?: never;
    };

/** The draft a keyed dialog's submission writes. */
interface MutationDialogScope {
  /**
   * The draft a keyed write's submission writes (`useIdempotencyKeys`): the dialog holds one
   * idempotency key for it from its first attempt until an accepted answer, passes it to its
   * submission, and gives the draft up when the person dismisses it with nothing possibly
   * written. While open, the dialog holds the scope (`useIdempotencyDraftOwner`).
   */
  idempotencyScope: string;
}

/**
 * A dialog's submission with the draft it writes: without `idempotencyScope` it is called with no
 * key; with one, with the draft's.
 */
export type MutationDialogDraft<TValues, TResult, TData = TResult> =
  | ({ idempotencyScope?: undefined } & MutationDialogSubmission<TValues, TResult, TData>)
  | (MutationDialogScope & MutationDialogSubmission<TValues, TResult, TData, true>);

/** A dialog whose submission writes no keyed draft: it is called with no idempotency key. */
export type MutationDialogProps<
  TValues extends Record<string, unknown>,
  TResult = unknown,
  TData = TResult,
> = MutationDialogBaseProps<TValues, TResult> & { idempotencyScope?: undefined } & MutationDialogSubmission<TValues, TResult, TData>;

/** A dialog whose submission writes a keyed draft (`idempotencyScope`): it is called with the draft's key, a `string`. */
export type KeyedMutationDialogProps<
  TValues extends Record<string, unknown>,
  TResult = unknown,
  TData = TResult,
> = MutationDialogBaseProps<TValues, TResult> & MutationDialogScope & MutationDialogSubmission<TValues, TResult, TData, true>;

interface MutationDialogBaseProps<
  TValues extends Record<string, unknown>,
  TResult,
> {
  /** Controlled visibility. Omit with `trigger` to let the dialog own it. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Native trigger; when supplied, the dialog owns open state and focus pairing. Passing none
   *  while the dialog is open keeps it open and mounted until it closes. */
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
 * While a submission runs the dialog cannot be dismissed, and dismissing typed values asks
 * first; a route move asks first in both cases (`DialogForm`, so it renders under the app's
 * router and `ModalsHost`, as every app page does). An answer that lands after the dialog went
 * away is still reported (`useLateAnswerReport`); only the dialog's own close and `onSubmitted`
 * are skipped. A `trigger`-owned dialog stays mounted while open when its owner keeps rendering
 * it without a trigger: hide the trigger by passing none, not by unmounting the dialog.
 * @kind surface
 */
export function MutationDialog<
  TValues extends Record<string, unknown>,
  TResult = unknown,
  TData = TResult,
>(props: MutationDialogProps<TValues, TResult, TData> | KeyedMutationDialogProps<TValues, TResult, TData>): React.ReactElement | null {
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
 * generic error notification off (the dialog renders the refusal), and a keyed one with its
 * draft's `idempotencyKey`, which it requires. The submission's result is awaited, so every
 * outcome, a throw included, settles the returned promise. A mutation that resolves no data does
 * not acknowledge the dialog: it fails with `fallback`.
 * @kind null
 * @reason Runtime infrastructure: the dialog's submission contract, shared with its test double.
 */
export async function submitMutationDialog<TValues, TResult, TData>(
  dialog: MutationDialogDraft<TValues, TResult, TData>,
  values: TValues,
  fallback: string,
  idempotencyKey?: string,
): Promise<FormSubmitResult<TResult>> {
  const answer = (data: TData | undefined): FormSubmitResult<TResult> => {
    if (data == null) return savedFormSubmitResult<TResult>(null, fallback);
    return dialog.mutationResult ? dialog.mutationResult(data) : { status: "ok", data: data as unknown as TResult };
  };
  if (dialog.idempotencyScope === undefined) {
    if (!dialog.mutation) return dialog.onSubmit(values, SURFACE_RENDERS_FAILURE);
    return answer(await dialog.mutation(values, SURFACE_RENDERS_FAILURE));
  }
  if (idempotencyKey === undefined) throw new Error(`The keyed dialog "${dialog.idempotencyScope}" was submitted without its draft's key.`);
  if (!dialog.mutation) return dialog.onSubmit(values, SURFACE_RENDERS_FAILURE, idempotencyKey);
  return answer(await dialog.mutation(values, SURFACE_RENDERS_FAILURE, idempotencyKey));
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
  idempotencyScope,
}: Omit<MutationDialogBaseProps<TValues, TResult>, "open" | "onOpenChange">
  & MutationDialogDraft<TValues, TResult, TData> & {
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
      markMemberErrors(errors, visibleFields(fields, formValues));
      return Object.keys(errors).length ? { values: {}, errors } : { values: formValues, errors: {} };
    },
  });
  const session = React.useRef(0);
  const submittingRef = React.useRef(false);
  // Set when an accepted submit closes the dialog: its `onSubmitted` may move on in the same tick,
  // before the close renders, and the dialog then holds nothing a move would lose.
  const acceptedRef = React.useRef(false);
  const openRef = useLatestRef(open);
  const previousOpen = React.useRef(open);
  React.useEffect(() => {
    if (previousOpen.current !== open) {
      previousOpen.current = open;
      session.current += 1;
      submittingRef.current = false;
      acceptedRef.current = false;
      form.reset(initialDialogValues(fields, initialValues));
      form.clearErrors();
    }
  }, [fields, form, initialValues, open]);
  const values = useWatch({ control: form.control });
  const shownFields = visibleFields(fields, values);
  const renderedFieldNames = shownFields.filter((field) => !field.hidden).map((field) => field.name);
  const submitting = form.formState.isSubmitting;
  const errors = formErrorsFromForm(form.formState.errors);
  const fieldsReady = form.formState.isValid || (!form.formState.isDirty
    && shownFields
      .filter((field) => !field.readOnly && !field.readOnlyWhen?.(values))
      .every((field) => !field.required && !field.presenceRequired));
  const ready = fieldsReady && (canSubmit?.(values) ?? true);
  const mounted = React.useRef(true);
  React.useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const dirty = form.formState.isDirty;
  const holdsWork = React.useCallback(
    () => openRef.current && !acceptedRef.current && (form.formState.isDirty || submittingRef.current),
    [form, openRef],
  );
  const reportLate = useLateAnswerReport();
  const keys = useIdempotencyKeys();
  useIdempotencyDraftOwner(open && idempotencyScope !== undefined ? idempotencyScope : null);
  const footer = ({ dismiss, dismissRefusal }: DialogFormFooterContext) => (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        disabledReason={dismissRefusal}
        onClick={dismiss}
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
    // Whether the write was sent: an answer that lands after the dialog went away is still reported.
    let sent = false;
    const current = () => mounted.current && session.current === submittedSession;
    try {
      const submittedValues = parseValues(collected);
      const validation = await validate?.(submittedValues, SURFACE_RENDERS_FAILURE);
      if (!mounted.current || session.current !== submittedSession) return;
      if (validation) {
        applyFormErrors(form, invalidFormSubmit(validation), errorOptions);
        await allowRootErrorRetry();
        return;
      }
      const dialog = { idempotencyScope, ...(mutation ? { mutation, mutationResult } : { onSubmit }) } as MutationDialogDraft<TValues, TResult, TData>;
      sent = true;
      const submitted = (idempotencyKey?: string) => submitMutationDialog(dialog, submittedValues, errorOptions.fallback, idempotencyKey);
      const result = idempotencyScope
        ? await keys.send(idempotencyScope, submitted, (answer) => answer.status === "ok")
        : await submitted();
      if (!current()) {
        // The dialog went away while the write ran: its answer is still reported, so nobody
        // writes it again, or trusts it went out, unaware; its close and `onSubmitted` are the dialog's own.
        reportLate(result, errorOptions.fallback);
        return;
      }
      if (applyFormErrors(form, result, errorOptions)) {
        await allowRootErrorRetry();
        return;
      }
      accepted = { result, values: submittedValues };
    } catch (cause) {
      if (current()) {
        applyFormErrors(form, formSubmitError(cause, errorOptions.fallback), errorOptions);
        await allowRootErrorRetry();
      } else if (sent) {
        reportLate({ thrown: cause }, errorOptions.fallback);
      }
    } finally {
      if (session.current === submittedSession) submittingRef.current = false;
    }
    if (accepted) {
      if (closeOnSubmit) acceptedRef.current = true;
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
      onOpenChange={(next) => {
        // A dismissal gives the draft up; the accepted close has ended it already.
        if (!next && idempotencyScope) keys.discard(idempotencyScope);
        onOpenChange(next);
      }}
      pending={submitting}
      dirty={dirty}
      holdsWork={holdsWork}
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
      <ErrorBanner messages={errors} />
    </DialogForm>
  );
}

/** A dialog's errors for the rules its fields fail, each field's found by `fieldRuleErrors` and
 * worded by `fieldErrorsFromRules`. As the record form's check (`formRuleErrors`) does, it asks
 * only the fields shown for `values` (`isFieldControlVisible`) that the person can change. It
 * differs from the record form's check in four facts only: it decides read-only by the field's
 * `readOnly` and `readOnlyWhen`, where the record form reads the resolved field's `readOnly`
 * alone; it reads each name as one flat key, where the record form reads dotted paths (both read
 * an own key holding `undefined` as absent); it counts the field's own `required` flag alone,
 * where the record form adds the model's required-on-create names; and it checks constraints only
 * with `validateConstraints` (a schema-resolved form leaves them and their messages to that
 * resolver), where the record form always does.
 * @kind null
 * @reason Descriptor validation shared by form surfaces.
 */
export function dialogRuleErrors(
  fields: readonly DescriptorField[] | ((values: Record<string, unknown>) => readonly DescriptorField[]),
  values: Record<string, unknown>,
  t: UiTranslate,
  validateConstraints = true,
): FieldErrors<Record<string, unknown>> {
  const resolved = visibleFields(typeof fields === "function" ? fields(values) : fields, values);
  const editable = resolved.filter((field) => isFieldControlVisible(field, values) && !field.readOnly && !field.readOnlyWhen?.(values));
  return fieldErrorsFromRules(editable.flatMap((field) => {
    const value = values[field.name];
    return fieldRuleErrors(
      field,
      { value, present: Object.hasOwn(values, field.name) && value !== undefined },
      { required: Boolean(field.required), validateConstraints },
    );
  }), t);
}

/** Each field's seed: its initial value, else its default, else `null` where it is nullable or its empty value; an omittable or presence-required field, and one that may be left out whose declared type refuses its seed (`refusesSeed`), stays absent. */
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
      const seed = field.nullable ? null : emptyValueForField(field);
      if (!refusesSeed(field, seed)) values[field.name] = seed;
    }
  }
  return values;
}

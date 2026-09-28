import * as React from "react";
import { FormProvider, set, useForm, useWatch, type FieldErrors } from "react-hook-form";
import { stringValue as wireStringValue } from "@angee/refine";
import { format } from "date-fns";

import { DialogForm } from "../../fragments/DialogForm";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { Button } from "../../ui/button";
import type { DialogPlacement, DialogSize } from "../../ui/dialog";
import { useToast } from "../../feedback";
import { useUiT } from "../../i18n";
import { dateFromUnknown } from "../../widgets/date-format";
import { emptyValueForField, isStructuredPresenceField, structuredFieldErrorPaths } from "./field-values";
import { DescriptorFieldList, resolveDescriptorFields, type DescriptorField } from "./DescriptorFieldList";
import { applyFormErrors, formSubmitError, invalidFormSubmit, type FormSubmitResult, type ValidationErrors } from "./validation-errors";

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
 * boundary: it preserves the picked local wall time and adds that instant's
 * local UTC offset, so Django never receives a naive datetime.
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
    const offsetMinutes = -parsed.getTimezoneOffset();
    const offsetSign = offsetMinutes < 0 ? "-" : "+";
    const absoluteOffset = Math.abs(offsetMinutes);
    const offsetHours = String(Math.floor(absoluteOffset / 60)).padStart(2, "0");
    const offsetRemainder = String(absoluteOffset % 60).padStart(2, "0");
    return `${format(parsed, "yyyy-MM-dd'T'HH:mm:ss")}${offsetSign}${offsetHours}:${offsetRemainder}`;
  },
} as const;

export interface MutationDialogProps<
  TValues extends Record<string, unknown>,
  TResult = unknown,
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
  /** Optional authoritative validation step that can bind errors to declared fields. */
  validate?: (
    values: TValues,
  ) => ValidationErrors | null | undefined | Promise<ValidationErrors | null | undefined>;
  onSubmit: (values: TValues) => FormSubmitResult<TResult> | Promise<FormSubmitResult<TResult>>;
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
 * widget registry.
 */
export function MutationDialog<
  TValues extends Record<string, unknown>,
  TResult = unknown,
>(props: MutationDialogProps<TValues, TResult>): React.ReactElement | null {
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

function MutationDialogInstance<TValues extends Record<string, unknown>, TResult>({
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
  onSubmitted,
  closeOnSubmit = true,
  canSubmit,
  size = "md",
  placement = "prompt",
  trigger,
}: Omit<MutationDialogProps<TValues, TResult>, "open" | "onOpenChange"> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}): React.ReactElement {
  const t = useUiT();
  const toast = useToast();
  const form = useForm<Record<string, unknown>>({
    defaultValues: initialDialogValues(fields, initialValues),
    mode: "onChange",
    resolver: (formValues) => {
      const resolved = resolveDescriptorFields(fields, formValues);
      const editable = resolved.filter((field) => !field.readOnly && !field.readOnlyWhen?.(formValues));
      const missing = editable.flatMap((field) => {
        if (isStructuredPresenceField(field)) {
          const value = formValues[field.name];
          const present = Object.hasOwn(formValues, field.name) && !(field.omittable && value === undefined);
          return structuredFieldErrorPaths(field, value, present);
        }
        return field.required && emptyDialogValue(formValues[field.name]) ? [field.name] : [];
      });
      return missing.length ? {
        values: {}, errors: requiredDialogErrors(missing, t("form.required")),
      } : { values: formValues, errors: {} };
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
  const visibleFields = resolveDescriptorFields(fields, values);
  const renderedFieldNames = visibleFields.filter((field) => !field.hidden).map((field) => field.name);
  const submitting = form.formState.isSubmitting;
  const error = form.formState.errors.root?.server?.message ?? null;
  const fieldsReady = form.formState.isValid || (!form.formState.isDirty
    && visibleFields
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
        disabled={!ready || submitting}
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
      await form.trigger(visibleFields.map((field) => field.name));
    }
  };

  const submitReady = form.handleSubmit(async (collected) => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    const submittedSession = session.current;
    form.clearErrors();
    let accepted: { result: Extract<FormSubmitResult<TResult>, { status: "ok" }>; values: TValues } | undefined;
    try {
      const submittedValues = parseValues(collected);
      const validation = await validate?.(submittedValues);
      if (!mounted.current || session.current !== submittedSession) return;
      if (validation) {
        applyFormErrors(form, invalidFormSubmit(validation), errorOptions);
        await allowRootErrorRetry();
        return;
      }
      const result = await onSubmit(submittedValues);
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
      <FormProvider {...form}>
        <DescriptorFieldList resolvedFields={visibleFields} />
      </FormProvider>
      <ErrorBanner description={error} />
    </DialogForm>
  );
}

function requiredDialogErrors(names: readonly string[], message: string): FieldErrors<Record<string, unknown>> {
  const errors: FieldErrors<Record<string, unknown>> = {};
  for (const name of names) set(errors, name, { type: "required", message });
  return errors;
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

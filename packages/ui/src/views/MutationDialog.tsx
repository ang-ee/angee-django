import * as React from "react";

import { errorMessage } from "../feedback";
import { DialogForm } from "../fragments/DialogForm";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { Button } from "../ui/button";
import { FieldDescription, FieldLabel, FieldRoot } from "../ui/field";
import type { DialogPlacement, DialogSize } from "../ui/dialog";
import { useUiT } from "../i18n";
import { FieldDescriptorControl } from "./field-descriptor-control";
import type { FieldDescriptor } from "./page";

export interface MutationDialogField extends FieldDescriptor {
  /** Client-side gate for simple mutation dialogs. Server validation remains authoritative. */
  required?: boolean;
  /** Disable editing for this field against the current dialog values. */
  readOnlyWhen?: (values: Record<string, unknown>) => boolean;
}

export interface MutationDialogProps<
  TValues extends Record<string, unknown> = Record<string, unknown>,
  TResult = unknown,
> {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description?: React.ReactNode;
  fields: readonly MutationDialogField[];
  /** Seeds a subset of the field values; `fields` defines the value keys, so a
   * partial seed must not narrow `TValues` via inference. */
  initialValues?: NoInfer<Partial<TValues>>;
  submitLabel: React.ReactNode;
  submittingLabel?: React.ReactNode;
  cancelLabel?: React.ReactNode;
  errorFallback?: string;
  onSubmit: (values: TValues) => TResult | Promise<TResult>;
  onSubmitted?: (result: TResult, values: TValues) => void;
  closeOnSubmit?: boolean;
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
  TValues extends Record<string, unknown> = Record<string, unknown>,
  TResult = unknown,
>({
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
  onSubmit,
  onSubmitted,
  closeOnSubmit = true,
  size = "md",
  placement = "prompt",
}: MutationDialogProps<TValues, TResult>): React.ReactElement {
  const t = useUiT();
  const [values, setValues] = React.useState<Record<string, unknown>>(() =>
    initialDialogValues(fields, initialValues),
  );
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const wasOpenRef = React.useRef(open);

  React.useEffect(() => {
    if (open && !wasOpenRef.current) {
      setValues(initialDialogValues(fields, initialValues));
      setError(null);
    }
    if (!open && wasOpenRef.current) {
      setValues(initialDialogValues(fields, initialValues));
      setError(null);
      setSubmitting(false);
    }
    wasOpenRef.current = open;
  }, [fields, initialValues, open]);

  const ready = fields.every(
    (field) => !field.required || !emptyDialogValue(values[field.name]),
  );
  const footer = (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => onOpenChange(false)}
      >
        {cancelLabel ?? t("dialog.cancel")}
      </Button>
      <Button
        type="submit"
        variant="primary"
        size="sm"
        disabled={!ready || submitting}
      >
        {submitting ? (submittingLabel ?? submitLabel) : submitLabel}
      </Button>
    </>
  );

  async function submit(
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (!ready || submitting) return;
    setSubmitting(true);
    setError(null);
    const submittedValues = values as TValues;
    try {
      const result = await onSubmit(submittedValues);
      onSubmitted?.(result, submittedValues);
      if (closeOnSubmit) onOpenChange(false);
    } catch (cause) {
      setError(errorMessage(cause, errorFallback ?? t("error.generic")));
    } finally {
      setSubmitting(false);
    }
  }

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
    >
      {fields.map((field) => {
        const readOnly =
          field.readOnly || field.readOnlyWhen?.(values) || submitting;
        return (
          <FieldRoot key={field.name}>
            <FieldLabel required={field.required}>
              {field.label ?? field.name}
            </FieldLabel>
            <FieldDescriptorControl
              field={field}
              value={values[field.name]}
              readOnly={readOnly}
              onChange={(next) =>
                setValues((current) => ({ ...current, [field.name]: next }))
              }
            />
            {field.description ? (
              <FieldDescription>{field.description}</FieldDescription>
            ) : null}
          </FieldRoot>
        );
      })}
      <ErrorBanner description={error} />
    </DialogForm>
  );
}

function initialDialogValues(
  fields: readonly MutationDialogField[],
  initialValues: Record<string, unknown> | undefined,
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const field of fields) {
    values[field.name] =
      initialValues?.[field.name] ?? emptyValueForField(field);
  }
  return values;
}

/**
 * The empty starting value for a dialog field, by its widget shape (list widgets
 * start `[]`, switches `false`, everything else `""`). MutationDialog owns the
 * dialog value ceremony; the typed-args action form seeds through the same rule.
 */
export function emptyValueForField(
  field: Pick<FieldDescriptor, "widget" | "kind">,
): unknown {
  if (field.widget === "tagInput") return [];
  if (field.kind === "switch" || field.widget === "switch") return false;
  return "";
}

/** Whether a dialog value counts as unfilled for the required-submit gate. */
export function emptyDialogValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

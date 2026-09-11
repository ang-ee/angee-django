import * as React from "react";
import { Controller, set, useForm, useWatch, type FieldErrors } from "react-hook-form";
import {
  canonicalModelLabelOrNull,
  modelMetadataForLabel,
  useSchemaFieldMetadata,
} from "@angee/metadata";
import type { CrudFilter } from "@refinedev/core";
import { stringValue as wireStringValue } from "@angee/refine";
import { format } from "date-fns";

import { errorMessage } from "../../feedback";
import { DialogForm } from "../../fragments/DialogForm";
import { ErrorBanner } from "../../fragments/ErrorBanner";
import { Button } from "../../ui/button";
import {
  FieldDescription,
  FieldError,
  FieldLabel,
  FieldRoot,
} from "../../ui/field";
import type { DialogPlacement, DialogSize } from "../../ui/dialog";
import { useUiT } from "../../i18n";
import { relationValueId } from "../../widgets/types";
import { dateFromUnknown } from "../../widgets/date-format";
import { FieldDescriptorControl } from "./field-descriptor-control";
import type { FormSpecFieldDescriptor } from "./form-spec";
import { relationFieldInfoForResource } from "../resource/model-metadata-defaults";
import { RelationPicker, type RelationCreateConfig } from "../relation/RelationPicker";
import { useRelationOptions } from "../relation/relation-options";
import type { FieldDescriptor } from "../page";
import { directDottedPathMessages } from "./validation-errors";
import { fieldErrorMessages, resolveField } from "./form-view-model";
import { emptyValueForField, isStructuredPresenceField, structuredFieldErrorPaths } from "./field-values";
import { DescriptorPresenceControl } from "./descriptor-presence-control";

export { emptyValueForField } from "./field-values";

/** What a dialog field needs to offer (and optionally create) a related row. */
export interface MutationDialogRelation {
  /** Related model label, e.g. `"integrate.Credential"`. */
  resource: string;
  /** Field shown as the option label; defaults to the model's record representation. */
  labelField?: string;
  /**
   * Server-side filters narrowing which rows are offered — for a target holding
   * more kinds of row than this field accepts (see `useRelationOptions`).
   */
  filters?: readonly CrudFilter[];
  /**
   * Enables the in-place "Create …" affordance. Unlike a form's auto-wired
   * relation field, a dialog states this explicitly: the dialog is not a model
   * form, so there is no metadata to derive creatability from.
   */
  create?: RelationCreateConfig;
}

export interface MutationDialogField extends FieldDescriptor {
  /** Client-side gate for simple mutation dialogs. Server validation remains authoritative. */
  required?: boolean;
  /** JSON-schema presence facts used by FormSpec-backed fields. */
  nullable?: boolean;
  omittable?: boolean;
  hasDefault?: boolean;
  /** Disable editing for this field against the current dialog values. */
  readOnlyWhen?: (values: Record<string, unknown>) => boolean;
  /**
   * Render this field as a searchable relation picker over `relation.resource`
   * instead of through the widget registry; the value is the selected row's
   * public id. The dialog analog of a form's `many2one` field — but it only
   * selects and creates, offering neither the pencil nor the follow arrow, since
   * a dialog must not navigate away from itself mid-edit.
   */
  relation?: MutationDialogRelation;
  /**
   * Render an addon-supplied control in place of the registry widget, while the
   * dialog keeps owning the surrounding label/description/error/required chrome
   * and the submit lifecycle. The seam for a field whose options are neither a
   * static list nor a resource relation — e.g. candidates searched live from a
   * remote host — so the addon that owns that vocabulary supplies the control
   * without the framework primitive learning the domain.
   *
   * Takes precedence over `relation`; `dialogValues` lets the control react to
   * the dialog's other fields (a picker scoped by a bridge chosen above it).
   */
  control?: (props: MutationDialogControlProps) => React.ReactElement;
  /** How a custom control receives its authored field label. */
  controlLabelMode?: "input" | "group";
}

/** What {@link MutationDialogField.control} receives to render one dialog field. */
export interface MutationDialogControlProps {
  id: string;
  value: unknown;
  readOnly: boolean;
  controlRef?: (target: import("../../widgets").WidgetFocusTarget | null) => void;
  describedBy: string | undefined;
  /** Present when the custom control declares `controlLabelMode: "group"`. */
  labelledBy: string | undefined;
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  /** Every current dialog value, so a control can scope itself by a sibling field. */
  dialogValues: Record<string, unknown>;
}

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
  fields: readonly MutationDialogField[];
  /** Seeds raw control state; parsed mutation variables may use different keys. */
  initialValues?: Readonly<Record<string, unknown>>;
  submitLabel: React.ReactNode;
  submittingLabel?: React.ReactNode;
  cancelLabel?: React.ReactNode;
  errorFallback?: string;
  /** Decode raw control state before it crosses the authored-mutation boundary. */
  parseValues: MutationDialogParseValues<TValues>;
  onSubmit: (values: TValues) => TResult | Promise<TResult>;
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
  const form = useForm<Record<string, unknown>>({
    defaultValues: initialDialogValues(fields, initialValues),
    mode: "onChange",
    resolver: (formValues) => {
      const resolved = fields.map((field) => ({
        ...resolveField(field, formValues),
        name: field.name,
      } as MutationDialogField));
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
  React.useEffect(() => {
    if (!open) {
      session.current += 1;
      submittingRef.current = false;
      form.reset(initialDialogValues(fields, initialValues));
      form.clearErrors();
    }
  }, [fields, form, initialValues, open]);
  const values = useWatch({ control: form.control });
  const submitting = form.formState.isSubmitting;
  const error = form.formState.errors.root?.server?.message ?? null;
  const fieldsReady = form.formState.isValid || (!form.formState.isDirty
    && fields.every((field) => !field.required && !field.presenceRequired));
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

  const submitReady = form.handleSubmit(async (collected) => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    const submittedSession = session.current;
    form.clearErrors();
    try {
      const submittedValues = parseValues(collected);
      const result = await onSubmit(submittedValues);
      if (!mounted.current || session.current !== submittedSession) return;
      onSubmitted?.(result, submittedValues);
      if (closeOnSubmit) onOpenChange(false);
    } catch (cause) {
      if (mounted.current && session.current === submittedSession) {
        form.setError("root.server", {
          type: "server",
          message: errorMessage(cause, errorFallback ?? t("error.generic")),
        });
        // Root transport errors must not lock a valid form out of retrying.
        // Revalidate the fields while retaining the root error for display.
        await form.trigger(fields.map((field) => field.name));
      }
    } finally {
      if (session.current === submittedSession) submittingRef.current = false;
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
      {fields.map((declaredField) => {
        const field = {
          ...resolveField(declaredField, values),
          name: declaredField.name,
        } as MutationDialogField;
        return (
          <Controller key={field.name} name={field.name} control={form.control}
            render={({ field: control, fieldState }) => (
              <LabeledDescriptorField
                field={field}
                value={control.value}
                dialogValues={values}
                messages={fieldState.error ? fieldErrorMessages(
                  [fieldState.error],
                  field.objectTemplate || field.itemTemplate || "rowTemplate" in field ? field.name : undefined,
                ) : []}
                readOnly={field.readOnly || field.readOnlyWhen?.(values) || submitting}
                onChange={control.onChange}
              />
            )}
          />
        );
      })}
      <ErrorBanner description={error} />
    </DialogForm>
  );
}

function requiredDialogErrors(names: readonly string[], message: string): FieldErrors<Record<string, unknown>> {
  const errors: FieldErrors<Record<string, unknown>> = {};
  for (const name of names) set(errors, name, { type: "required", message });
  return errors;
}

/**
 * Field chrome for one descriptor: label, description, invalid state, and
 * messages around the bare registry-rendering {@link FieldDescriptorControl}.
 */
export function LabeledDescriptorField({
  field,
  value,
  dialogValues,
  readOnly,
  messages = [],
  showLabel = true,
  showDescription = true,
  onChange,
  onCommit,
  controlRef,
}: {
  field: MutationDialogField & {
    rowTemplate?: readonly FormSpecFieldDescriptor[];
    objectTemplate?: readonly FormSpecFieldDescriptor[];
    itemTemplate?: FormSpecFieldDescriptor;
    nullable?: boolean;
    omittable?: boolean;
    hasDefault?: boolean;
  };
  value: unknown;
  /** The sibling dialog values a `field.control` may scope itself by. */
  dialogValues?: Record<string, unknown>;
  readOnly?: boolean;
  messages?: readonly string[];
  showLabel?: boolean;
  showDescription?: boolean;
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  controlRef?: (target: import("../../widgets").WidgetFocusTarget | null) => void;
}): React.ReactElement {
  const generatedId = React.useId();
  const controlId = `mutation-field-${generatedId}`;
  const labelId = `${controlId}-label`;
  const isCompositeField = field.rowTemplate !== undefined || field.objectTemplate !== undefined || field.itemTemplate !== undefined;
  const groupLabel = field.controlLabelMode === "group" || isCompositeField;
  const displayedMessages = isCompositeField
    ? directDottedPathMessages(messages, field.name)
    : messages;
  const descriptionId = showDescription && field.description
    ? `${controlId}-description`
    : undefined;
  const errorId = displayedMessages.length > 0
    ? `${controlId}-error`
    : undefined;
  const describedBy =
    [descriptionId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <FieldRoot invalid={displayedMessages.length > 0}>
      {showLabel ? (
        <FieldLabel
          id={groupLabel ? labelId : undefined}
          htmlFor={isCompositeField || groupLabel ? undefined : controlId}
          required={field.required}
        >
          {field.label ?? field.name}
        </FieldLabel>
      ) : null}
      <DescriptorPresenceControl field={field} value={value} readOnly={readOnly} onChange={onChange} onCommit={onCommit} controlRef={controlRef}>
      {field.control ? (
        field.control({
          id: controlId,
          value,
          readOnly: Boolean(readOnly),
          controlRef,
          describedBy,
          labelledBy: groupLabel ? labelId : undefined,
          onChange,
          onCommit,
          dialogValues: dialogValues ?? {},
        })
      ) : field.relation ? (
        <MutationDialogRelationControl
          controlId={controlId}
          describedBy={describedBy}
          field={field}
          relation={field.relation}
          value={value}
          readOnly={readOnly}
          onChange={onChange}
          onCommit={onCommit}
          controlRef={controlRef}
        />
      ) : (
        <FieldDescriptorControl
          field={field}
          value={value}
          messages={messages}
          readOnly={readOnly}
          controlProps={{
            id: controlId,
            ...(describedBy ? { "aria-describedby": describedBy } : {}),
            ...(groupLabel ? { "aria-labelledby": labelId } : {}),
            ...(field.required ? { "aria-required": true } : {}),
          }}
          onChange={onChange}
          onCommit={onCommit}
          controlRef={controlRef}
        />
      )}
      </DescriptorPresenceControl>
      {showDescription && field.description ? (
        <FieldDescription id={descriptionId}>{field.description}</FieldDescription>
      ) : null}
      {displayedMessages.length > 0 ? (
        <FieldError id={errorId} match>
          {displayedMessages.join(", ")}
        </FieldError>
      ) : null}
    </FieldRoot>
  );
}

/**
 * One dialog field rendered as a relation picker: the offered rows come from the
 * related resource's list root (narrowed by the field's `filters`), and "Create …"
 * opens the field's own create form. The option query is deferred until the
 * popover first opens, except that an existing bare-id value eagerly fetches its
 * label. A dialog with an empty untouched relation still performs no work.
 */
function MutationDialogRelationControl({
  controlId,
  describedBy,
  field,
  relation,
  value,
  readOnly,
  onChange,
  onCommit,
  controlRef,
}: {
  controlId: string;
  describedBy?: string;
  field: MutationDialogField;
  relation: MutationDialogRelation;
  value: unknown;
  readOnly?: boolean;
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  controlRef?: (target: import("../../widgets").WidgetFocusTarget | null) => void;
}): React.ReactElement {
  const [opened, setOpened] = React.useState(false);
  const metadata = useSchemaFieldMetadata();
  const resource = React.useMemo(
    () =>
      canonicalModelLabelOrNull(
        metadata.resources ?? [],
        relation.resource,
        "mutation dialog relation",
      ) ?? "",
    [metadata, relation.resource],
  );
  const model = React.useMemo(
    () => modelMetadataForLabel(metadata, resource),
    [metadata, resource],
  );
  const info = React.useMemo(
    () => relationFieldInfoForResource(resource, model),
    [resource, model],
  );
  const create = React.useMemo(
    () => {
      if (!relation.create) return undefined;
      const createResource = canonicalModelLabelOrNull(
        metadata.resources ?? [],
        relation.create.resource,
        "mutation dialog relation create",
      );
      return createResource
        ? { ...relation.create, resource: createResource }
        : undefined;
    },
    [metadata, relation.create],
  );
  const selectedValue = relationValueId(value);
  const { list, options } = useRelationOptions(info, {
    // FormView can thread a selectedOption from its folded detail row. Dialog
    // descriptors carry bare ids, so a filled value eagerly loads the small
    // option set to resolve its label before the picker is opened.
    enabled: opened || Boolean(selectedValue),
    ...(relation.labelField ? { labelField: relation.labelField } : {}),
    ...(relation.filters ? { filters: relation.filters } : {}),
  });
  if (!info) {
    // Metadata not yet loaded / the resource is unknown in this schema: retain
    // the relation control's shape, but disable it rather than throwing from
    // render or presenting a text input that could submit an unvalidated id.
    return (
      <RelationPicker
        id={controlId}
        value={selectedValue}
        options={[]}
        readOnly
        placeholder={field.placeholder}
        aria-label={typeof field.label === "string" ? field.label : field.name}
        aria-describedby={describedBy}
        aria-required={field.required || undefined}
        onChange={onChange}
        onCommit={onCommit}
      />
    );
  }
  return (
    <RelationPicker
      controlRef={controlRef}
      id={controlId}
      value={selectedValue}
      onChange={onChange}
      onCommit={onCommit}
      options={options}
      readOnly={readOnly}
      placeholder={field.placeholder}
      aria-label={typeof field.label === "string" ? field.label : field.name}
      aria-describedby={describedBy}
      aria-required={field.required || undefined}
      {...(create ? { create } : {})}
      onCreated={() => list.refetch()}
      onOpenChange={(open) => {
        if (open) setOpened(true);
      }}
    />
  );
}

function initialDialogValues(
  fields: readonly MutationDialogField[],
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

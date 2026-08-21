import * as React from "react";
import {
  canonicalModelLabelOrNull,
  modelMetadataForLabel,
  useSchemaFieldMetadata,
} from "@angee/metadata";
import type { CrudFilter } from "@refinedev/core";
import { stringValue as wireStringValue } from "@angee/refine";

import { errorMessage } from "../feedback";
import { DialogForm } from "../fragments/DialogForm";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { Button } from "../ui/button";
import {
  FieldDescription,
  FieldError,
  FieldLabel,
  FieldRoot,
} from "../ui/field";
import type { DialogPlacement, DialogSize } from "../ui/dialog";
import { useUiT } from "../i18n";
import { relationValueId } from "../widgets/types";
import { FieldDescriptorControl } from "./field-descriptor-control";
import type { FormSpecFieldDescriptor } from "./form-spec";
import { relationFieldInfoForResource } from "./model-metadata-defaults";
import { RelationPicker, type RelationCreateConfig } from "./RelationPicker";
import { useRelationOptions } from "./relation-options";
import type { FieldDescriptor } from "./page";
import { directDottedPathMessages } from "./validation-errors";

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
  describedBy: string | undefined;
  /** Present when the custom control declares `controlLabelMode: "group"`. */
  labelledBy: string | undefined;
  onChange: (value: unknown) => void;
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
 * input is reachable user validation.
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
} as const;

export interface MutationDialogProps<
  TValues extends Record<string, unknown>,
  TResult = unknown,
> {
  open: boolean;
  onOpenChange: (open: boolean) => void;
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
  parseValues,
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

  async function submit(
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (!ready || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const submittedValues = parseValues(values);
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
      {fields.map((field) => (
        <LabeledDescriptorField
          key={field.name}
          field={field}
          value={values[field.name]}
          dialogValues={values}
          readOnly={
            field.readOnly || field.readOnlyWhen?.(values) || submitting
          }
          onChange={(next) =>
            setValues((current) => ({ ...current, [field.name]: next }))
          }
        />
      ))}
      <ErrorBanner description={error} />
    </DialogForm>
  );
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
}: {
  field: MutationDialogField & {
    rowTemplate?: readonly FormSpecFieldDescriptor[];
  };
  value: unknown;
  /** The sibling dialog values a `field.control` may scope itself by. */
  dialogValues?: Record<string, unknown>;
  readOnly?: boolean;
  messages?: readonly string[];
  showLabel?: boolean;
  showDescription?: boolean;
  onChange: (value: unknown) => void;
}): React.ReactElement {
  const generatedId = React.useId();
  const controlId = `mutation-field-${generatedId}`;
  const labelId = `${controlId}-label`;
  const isRowsField = field.rowTemplate !== undefined;
  const groupLabel = field.controlLabelMode === "group";
  const displayedMessages = isRowsField
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
          htmlFor={isRowsField || groupLabel ? undefined : controlId}
          required={field.required}
        >
          {field.label ?? field.name}
        </FieldLabel>
      ) : null}
      {field.control ? (
        field.control({
          id: controlId,
          value,
          readOnly: Boolean(readOnly),
          describedBy,
          labelledBy: groupLabel ? labelId : undefined,
          onChange,
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
            ...(field.required ? { "aria-required": true } : {}),
          }}
          onChange={onChange}
        />
      )}
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
}: {
  controlId: string;
  describedBy?: string;
  field: MutationDialogField;
  relation: MutationDialogRelation;
  value: unknown;
  readOnly?: boolean;
  onChange: (value: unknown) => void;
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
      />
    );
  }
  return (
    <RelationPicker
      id={controlId}
      value={selectedValue}
      onChange={onChange}
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
    values[field.name] =
      initialValues?.[field.name] ?? emptyValueForField(field);
  }
  return values;
}

/**
 * The empty starting value for a descriptor field. Collection and object schema
 * kinds keep their JSON shape, numeric/unknown schema kinds avoid the empty
 * string rejected by Strawberry, switches/booleans start false, and remaining
 * scalar controls start `""`.
 */
export function emptyValueForField(
  field: Pick<FieldDescriptor, "widget" | "kind">,
): unknown {
  if (field.kind === "array" || field.widget === "tagInput") return [];
  if (field.kind === "object") return {};
  if (
    field.kind === "boolean" ||
    field.kind === "switch" ||
    field.widget === "switch"
  ) {
    return false;
  }
  if (
    field.kind === "integer" ||
    field.kind === "number" ||
    field.kind === "any"
  ) {
    if (field.widget === "select") return "";
    return null;
  }
  return "";
}

/** Whether a dialog value counts as unfilled for the required-submit gate. */
export function emptyDialogValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

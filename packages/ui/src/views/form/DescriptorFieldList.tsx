import * as React from "react";
import { Controller, useFormContext, useWatch } from "react-hook-form";
import { canonicalModelLabelOrNull, modelMetadataForLabel, useSchemaFieldMetadata } from "@angee/metadata";
import type { CrudFilter } from "@refinedev/core";

import { FieldDescription, FieldError, FieldLabel, FieldRoot } from "../../ui/field";
import { relationValueId, type WidgetFocusTarget } from "../../widgets/types";
import { FieldDescriptorControl } from "./field-descriptor-control";
import type { FormSpecFieldDescriptor } from "./form-spec";
import { relationFieldInfoForResource } from "../resource/model-metadata-defaults";
import { RelationPicker, type RelationCreateConfig } from "../relation/RelationPicker";
import { useRelationPickerOptions } from "../relation/relation-options";
import type { FieldDescriptor } from "../page";
import { directDottedPathMessages } from "./validation-errors";
import { fieldErrorMessages, isCompositeFieldDescriptor, isFieldVisible, resolveField } from "./form-view-model";
import { DescriptorPresenceControl } from "./descriptor-presence-control";

/** What a descriptor field needs to offer (and optionally create) a related row. */
export interface DescriptorFieldRelation {
  /** Canonical related model label. */
  resource: string;
  /** Field shown as the option label; defaults to the model's record representation. */
  labelField?: string;
  /**
   * Server-side filters narrowing which rows are offered — for a target holding
   * more kinds of row than this field accepts.
   */
  filters?: readonly CrudFilter[];
  /**
   * Enables native in-place creation, including an optional visible action.
   * A descriptor states creatability explicitly when no model field owns it.
   */
  create?: RelationCreateConfig;
}

export interface DescriptorField extends FieldDescriptor {
  /** Reset a variant's local editor draft while retaining the form-owned value. */
  controlKey?: React.Key;
  /** Disable editing for this field against the current form values. */
  readOnlyWhen?: (values: Record<string, unknown>) => boolean;
  /**
   * Describe the fields and initial values belonging to one discriminator value.
   * DescriptorFieldList removes the old/new branch paths before seeding the new
   * branch. Other form values and the discriminator itself remain untouched.
   */
  branchReset?: (value: unknown) => {
    fields: readonly string[];
    values: Record<string, unknown>;
  };
  /**
   * Render this field as a searchable relation picker over `relation.resource`
   * instead of through the widget registry; the value is the selected row's
   * public id. Selection and in-place creation keep the surrounding form open.
   */
  relation?: DescriptorFieldRelation;
  /**
   * Render an addon-supplied control in place of the registry widget, while the
   * renderer owns the surrounding label/description/error/required chrome.
   * The seam for a field whose options are neither a
   * static list nor a resource relation — e.g. candidates searched live from a
   * remote host — so the addon that owns that vocabulary supplies the control
   * without the framework primitive learning the domain.
   *
   * Takes precedence over `relation`; `dialogValues` lets the control react to
   * the form's other fields.
   */
  control?: (props: DescriptorFieldControlProps) => React.ReactElement;
  /** How a custom control receives its authored field label. */
  controlLabelMode?: "input" | "group";
}

/** What {@link DescriptorField.control} receives to render one descriptor field. */
export interface DescriptorFieldControlProps {
  id: string;
  value: unknown;
  readOnly: boolean;
  /** Whether the control has a displayed validation error. */
  invalid?: boolean;
  controlRef?: (target: WidgetFocusTarget | null) => void;
  describedBy: string | undefined;
  /** Present when the custom control declares `controlLabelMode: "group"`. */
  labelledBy: string | undefined;
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  /** Every current form value, so a control can scope itself by a sibling field. */
  dialogValues: Record<string, unknown>;
}

export type DescriptorFieldListProps = {
  readOnly?: boolean;
} & (
  | { fields: readonly DescriptorField[]; resolvedFields?: never }
  | { fields?: never; resolvedFields: readonly DescriptorField[] }
);

/** Render descriptors in the existing React Hook Form provider's value tree. */
export function DescriptorFieldList({ fields, resolvedFields, readOnly = false }: DescriptorFieldListProps): React.ReactElement {
  const form = useFormContext<Record<string, unknown>>();
  if (!form) throw new Error("DescriptorFieldList requires a React Hook Form FormProvider.");
  const values = useWatch({ control: form.control });
  const renderedFields = fields === undefined ? resolvedFields : resolveDescriptorFields(fields, values);
  return <>{renderedFields.map((field) => (
    <Controller key={field.name} name={field.name} control={form.control} exact={false}
      render={({ field: control, fieldState }) => (
        <LabeledDescriptorField
          key={field.controlKey}
          field={field}
          value={control.value}
          dialogValues={values}
          messages={fieldState.error ? fieldErrorMessages(
            [fieldState.error],
            isCompositeFieldDescriptor(field) ? field.name : undefined,
          ) : []}
          readOnly={readOnly || field.readOnly || field.readOnlyWhen?.(values)}
          disabled={form.formState.isSubmitting}
          controlRef={control.ref}
          onCommit={control.onBlur}
          onChange={(next) => {
            if (field.branchReset && !Object.is(next, control.value)) {
              const previous = field.branchReset(control.value);
              const branch = field.branchReset(next);
              form.unregister([...new Set([...previous.fields, ...branch.fields])].filter((name) => name !== field.name));
              for (const name of branch.fields) {
                if (name !== field.name && Object.hasOwn(branch.values, name)) {
                  form.setValue(name, branch.values[name]);
                }
              }
            }
            form.clearErrors(field.name);
            control.onChange(next);
            const seeds = field.prefill?.(next);
            if (!seeds) return;
            for (const [name, seed] of Object.entries(seeds)) {
              form.clearErrors(name);
              form.setValue(name, seed, {
                shouldDirty: true,
                shouldTouch: true,
                shouldValidate: true,
              });
            }
          }}
        />
      )}
    />
  ))}</>;
}

/** Resolve presentation without allowing a dynamic descriptor to rename its value. */
export function resolveDescriptorFields(
  fields: readonly DescriptorField[],
  values: Record<string, unknown>,
): DescriptorField[] {
  return fields
    .map((declared) => ({ ...declared, ...resolveField(declared, values), name: declared.name }))
    .filter((field) => isFieldVisible(field, values));
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
  disabled,
  messages = [],
  showLabel = true,
  showDescription = true,
  onChange: changeValue,
  onCommit,
  controlRef,
}: {
  field: DescriptorField & {
    rowTemplate?: readonly FormSpecFieldDescriptor[];
    objectTemplate?: readonly FormSpecFieldDescriptor[];
    itemTemplate?: FormSpecFieldDescriptor;
    addLabel?: string;
    removeLabel?: string;
  };
  value: unknown;
  /** The sibling form values a `field.control` may scope itself by. */
  dialogValues?: Record<string, unknown>;
  readOnly?: boolean;
  disabled?: boolean;
  messages?: readonly string[];
  showLabel?: boolean;
  showDescription?: boolean;
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  controlRef?: (target: WidgetFocusTarget | null) => void;
}): React.ReactElement | null {
  const generatedId = React.useId();
  if (field.hidden) return null;
  const onChange = (next: unknown) => {
    if (!readOnly && !disabled) changeValue(next);
  };
  const controlId = `mutation-field-${generatedId}`;
  const labelId = `${controlId}-label`;
  const isCompositeField = isCompositeFieldDescriptor(field);
  const groupLabel = field.controlLabelMode === "group" || isCompositeField;
  const displayedMessages = isCompositeField
    ? directDottedPathMessages(messages, field.name)
    : messages;
  const invalid = displayedMessages.length > 0;
  const descriptionId = showDescription && field.description
    ? `${controlId}-description`
    : undefined;
  const errorId = invalid
    ? `${controlId}-error`
    : undefined;
  const describedBy =
    [descriptionId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <FieldRoot invalid={invalid}>
      {showLabel ? (
        <FieldLabel
          id={groupLabel ? labelId : undefined}
          htmlFor={isCompositeField || groupLabel ? undefined : controlId}
          required={field.required}
        >
          {field.label ?? field.name}
        </FieldLabel>
      ) : null}
      <DescriptorPresenceControl field={field} value={value} readOnly={readOnly || disabled} onChange={onChange} onCommit={onCommit} controlRef={controlRef}>
      {field.control ? (
        field.control({
          id: controlId,
          value,
          readOnly: Boolean(readOnly || disabled),
          invalid,
          controlRef,
          describedBy,
          labelledBy: groupLabel ? labelId : undefined,
          onChange,
          onCommit,
          dialogValues: dialogValues ?? {},
        })
      ) : field.relation ? (
        <DescriptorRelationControl
          controlId={controlId}
          describedBy={describedBy}
          invalid={invalid}
          field={field}
          relation={field.relation}
          value={value}
          readOnly={readOnly || disabled}
          onChange={onChange}
          onCommit={onCommit}
          controlRef={controlRef}
        />
      ) : (
        <FieldDescriptorControl
          field={field}
          value={value}
          row={dialogValues}
          messages={messages}
          readOnly={readOnly}
          disabled={disabled}
          controlProps={{
            id: controlId,
            "aria-invalid": invalid || undefined,
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
      {invalid ? (
        <FieldError id={errorId} match>
          {displayedMessages.join(", ")}
        </FieldError>
      ) : null}
    </FieldRoot>
  );
}

/**
 * One descriptor field rendered as a relation picker: the offered rows come from the
 * related resource's list root (narrowed by the field's `filters`), and "Create …"
 * opens the field's own create form. The option query is deferred until the
 * popover first opens; an existing bare-id value resolves through its independent
 * record read. An empty untouched relation performs no work.
 */
function DescriptorRelationControl({
  controlId,
  describedBy,
  invalid,
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
  invalid: boolean;
  field: DescriptorField;
  relation: DescriptorFieldRelation;
  value: unknown;
  readOnly?: boolean;
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  controlRef?: (target: WidgetFocusTarget | null) => void;
}): React.ReactElement {
  const metadata = useSchemaFieldMetadata();
  const resource = React.useMemo(
    () =>
      canonicalModelLabelOrNull(
        metadata.resources ?? [],
        relation.resource,
        "descriptor relation",
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
  const optionInfo = React.useMemo(
    () => info && relation.labelField
      ? { ...info, labelField: relation.labelField }
      : info,
    [info, relation.labelField],
  );
  const create = React.useMemo(
    () => {
      if (!relation.create) return undefined;
      const createResource = canonicalModelLabelOrNull(
        metadata.resources ?? [],
        relation.create.resource,
        "descriptor relation create",
      );
      return createResource
        ? { ...relation.create, resource: createResource }
        : undefined;
    },
    [metadata, relation.create],
  );
  const selectedValue = relationValueId(value);
  const picker = useRelationPickerOptions(optionInfo, {
    value: selectedValue,
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
        aria-invalid={invalid || undefined}
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
      options={picker.options}
      readOnly={readOnly}
      placeholder={field.placeholder}
      aria-label={typeof field.label === "string" ? field.label : field.name}
      aria-describedby={describedBy}
      aria-invalid={invalid || undefined}
      aria-required={field.required || undefined}
      {...(create ? { create } : {})}
      onCreated={() => picker.list.refetch()}
      onOpenChange={picker.onOpenChange}
      onSearchChange={picker.onSearchChange}
      searchState={picker.searchState}
    />
  );
}

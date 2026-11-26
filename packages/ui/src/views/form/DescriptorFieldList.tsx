import * as React from "react";

import { useAppRuntime } from "../../runtime";
import { resolveFieldLabels } from "./field-labels";
import { Controller, useFormContext, useWatch } from "react-hook-form";
import { canonicalModelLabelOrNull, modelMetadataForLabel, useSchemaFieldMetadata } from "@angee/metadata";
import type { CrudFilter } from "@refinedev/core";
import type { ListQueryFieldArgument } from "@angee/refine";

import { FieldDescription, FieldError, FieldLabel, FieldRoot } from "../../ui/field";
import { relationValueId, type WidgetFocusTarget } from "../../widgets/types";
import { relationSelectedOption } from "../relation/relation-options";
import { FieldDescriptorControl } from "./field-descriptor-control";
import type { FormSpecFieldDescriptor } from "./form-spec";
import { relationFieldInfoForResource } from "../resource/model-metadata-defaults";
import { RelationPicker, type RelationCreateConfig } from "../relation/RelationPicker";
import { RelationFieldWidget } from "../relation/RelationFieldWidget";
import { RelationMultiFieldWidget } from "../relation/RelationMultiFieldWidget";
import type { FieldDescriptor } from "../page";
import { directDottedPathMessages } from "./validation-errors";
import { fieldErrorMessages, isCompositeFieldDescriptor, missingDraftValues, pathHolding, seedPreset, visibleFields, type FormValues } from "./form-view-model";
import { usePresetMemory } from "./ActionFormProvider";
import { DescriptorPresenceControl } from "./descriptor-presence-control";
import { RecordFieldMarkControl } from "./record-field-marks";

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
  /** Related-row context and status projected by the shared relation widgets. */
  optionDescriptionField?: string;
  optionStatusField?: string;
  optionStatusArguments?: readonly ListQueryFieldArgument[];
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
   * Re-initialize the fields that depend on this one when it changes, as if never touched: the
   * list unregisters the paths named for the old and for the new value (their errors, dirty and
   * touched state go with them) and writes the new value's initial `values` without validating.
   * Other form values and this field itself remain untouched. Not a preset: `prefill` writes what
   * follows from the person's choice visibly (validated, dirty, touched). Where the list also
   * unregisters what it stops showing (`unregisterHidden`), the old value's own fields already
   * leave with it; what stays this member's own is the quiet re-initialization: a branch the form
   * holds no default for, and a field both values name.
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

export interface DescriptorFieldListProps {
  /** The fields the list declares; it shows those visible for the form's values, resolved against them. */
  fields: readonly DescriptorField[];
  readOnly?: boolean;
  /**
   * Keep in the form's values only the fields the list shows: unregister, keeping its default,
   * each field the list declares or showed and does not show (from its first render on), and
   * give a field that shows again its default back as its value. A dialog sets it: its fields
   * are not shown only when they are hidden. Off, a field the list stops showing keeps its value,
   * as a list showing part of a larger form needs. The record form drops what a preset hides and
   * restores what returns with the same two calls (`afterFieldChange`), from its model, as its
   * controls also unmount for tabs and sections.
   */
  unregisterHidden?: boolean;
}

/** Render descriptors in the value tree of the enclosing ActionFormProvider's React Hook Form form.
 * @kind surface
 */
export function DescriptorFieldList({ fields, readOnly = false, unregisterHidden = false }: DescriptorFieldListProps): React.ReactElement {
  const form = useFormContext<Record<string, unknown>>();
  const memory = usePresetMemory();
  if (!form) throw new Error("DescriptorFieldList requires a React Hook Form FormProvider.");
  if (!memory) throw new Error("DescriptorFieldList requires an ActionFormProvider.");
  const values = useWatch({ control: form.control });
  const { i18n } = useAppRuntime();
  const renderedFields = resolveFieldLabels(visibleFields(fields, values), i18n);
  const { unregister } = form;
  const declaredKey = JSON.stringify(fields.map((field) => field.name));
  const shownKey = JSON.stringify(renderedFields.map((field) => field.name));
  const shownBefore = React.useRef("[]");
  // Before anything reads the values, drop each field the list declares or showed and does not
  // show, keeping its default: the record form's call. Not the Controller's `shouldUnregister`,
  // which unregisters without `keepDefaultValue`: a returning field would render empty while a
  // mount effect restores its value. Unmounting the list drops nothing.
  React.useLayoutEffect(() => {
    const before = JSON.parse(shownBefore.current) as string[];
    shownBefore.current = shownKey;
    if (!unregisterHidden) return;
    const shown = JSON.parse(shownKey) as string[];
    // A path a shown field still holds, or still lives under, stays: its value is that field's too.
    const hidden = [...new Set([...before, ...(JSON.parse(declaredKey) as string[])])]
      .filter((name) => pathHolding(shown, name) === null);
    if (hidden.length) unregister(hidden, { keepDefaultValue: true });
  }, [declaredKey, shownKey, unregister, unregisterHidden]);
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
            memory.noteEdit(field.name);
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
            // Controller accepts browser events as well as values. An object
            // field may itself declare "target", so always put the value in
            // the native event envelope to keep the entire object atomic.
            control.onChange({ target: { value: next } });
            seedPreset(field, next, form.getValues(), form.formState.defaultValues as FormValues | undefined, {
              // The list has no edit mode that locks a `createOnly` field, so it seeds as a create does.
              isCreate: true,
              memory,
              read: (path) => ({ dirty: form.getFieldState(path).isDirty, value: form.getValues(path) }),
              write: (path, seed, dirties) => {
                form.clearErrors(path);
                form.setValue(path, seed, { shouldDirty: dirties, shouldTouch: true, shouldValidate: true });
              },
            });
            // A field that shows again for the new values takes its default back here, before its
            // control renders: registering a control that has no value updates the form's state
            // while that control renders.
            if (unregisterHidden) {
              const loaded = (form.formState.defaultValues ?? {}) as FormValues;
              for (const [path, restored] of missingDraftValues(form.getValues(), fields, loaded)) {
                form.setValue(path, restored, { shouldDirty: true });
              }
            }
          }}
        />
      )}
    />
  ))}</>;
}

/**
 * Field chrome for one descriptor: label, description, invalid state, and
 * messages around the bare registry-rendering {@link FieldDescriptorControl}.
 * @kind surface
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
        <div className="flex items-center gap-1"><FieldLabel
          id={groupLabel ? labelId : undefined}
          htmlFor={isCompositeField || groupLabel ? undefined : controlId}
          required={field.required && !readOnly}
        >
          {field.label ?? field.name}
        </FieldLabel>
        <RecordFieldMarkControl field={field.name} label={field.label} /></div>
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
      ) : field.relation && (!field.widget || field.widget === "many2one" || field.widget === "many2many") ? (
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
            ...(field.required && !readOnly ? { "aria-required": true } : {}),
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
 * record read, and a value that is the related row (`{ id, <label field> }`) shows its
 * label with no read. An empty untouched relation performs no work. The field's
 * `relationEdit: null` declines the picker's inline edit, as on a record form.
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
  const controlProps = {
    id: controlId,
    "aria-describedby": describedBy,
    "aria-invalid": invalid || undefined,
    "aria-required": field.required || undefined,
  };
  if (!optionInfo) {
    // Metadata not yet loaded / the resource is unknown in this schema: retain
    // the relation control's shape, but disable it rather than throwing from
    // render or presenting a text input that could submit an unvalidated id.
    return (
      <RelationPicker
        {...controlProps}
        value={relationValueId(value)}
        options={[]}
        readOnly
        placeholder={field.placeholder}
        aria-label={typeof field.label === "string" ? field.label : field.name}
        onChange={onChange}
        onCommit={onCommit}
      />
    );
  }
  const common = {
    relation: optionInfo,
    filters: field.filters ?? relation.filters,
    create: create ?? null,
    fieldName: field.name,
    readOnly,
    onChange,
    onCommit,
    controlRef,
    controlProps,
    "aria-label": typeof field.label === "string" ? field.label : field.name,
  };
  return field.widget === "many2many"
    ? <RelationMultiFieldWidget {...common} value={Array.isArray(value) ? value : []} />
    : <RelationFieldWidget {...common}
      value={relationValueId(value)}
      // A value that is the related row carries its own label: shown without reading the record.
      selectedOption={relationSelectedOption(value, optionInfo.labelField)}
      edit={field.relationEdit}
      placeholder={field.placeholder}
      optionDescriptionField={relation.optionDescriptionField}
      optionStatusField={relation.optionStatusField}
      optionStatusArguments={relation.optionStatusArguments}
    />;
}

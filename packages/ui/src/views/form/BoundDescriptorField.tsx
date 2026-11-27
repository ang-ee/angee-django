import * as React from "react";
import { Controller, useWatch } from "react-hook-form";
import { useModelMetadata, useSchemaFieldMetadata } from "@angee/metadata";

import type { DescriptorField } from "./DescriptorFieldList";
import { LabeledDescriptorField } from "./DescriptorFieldList";
import { fieldErrorMessages, isFieldVisible, isFormValues, resolveField, type FormValues } from "./form-view-model";
import type { FormViewSaveSurface } from "./use-form-view-save";
import { fieldsWithMetadataDefaults } from "../resource/model-metadata-defaults";
import { isUnregisteredImplementationField } from "../resource/implementation-fields";
import { UnregisteredImplementationField } from "./record-field-marks";
import type { WidgetFocusTarget } from "../../widgets";

export interface BoundDescriptorFieldProps {
  /** The shared FormView surface that owns values, errors, dirtiness and save. */
  form: FormViewSaveSurface;
  /** Resource whose metadata owns the descriptor. */
  resource: string;
  /** Descriptor expressed relative to `scope`. */
  field: DescriptorField;
  /** Dotted object path containing the descriptor's sibling values. */
  scope?: string;
  readOnly?: boolean;
}

export interface BoundFormValueRenderProps {
  value: unknown;
  error?: string;
  messages: string[];
  controlRef: (target: WidgetFocusTarget | null) => void;
  onChange: (value: unknown) => void;
  onCommit: () => void;
}
export interface BoundFormValueProps {
  form: FormViewSaveSurface;
  name: string;
  children: (props: BoundFormValueRenderProps) => React.ReactElement;
}

/** Bind a domain-owned controlled editor to one value in FormView's RHF tree.
 * @kind surface
 */
export function BoundFormValue({ form: surface, name, children }: BoundFormValueProps): React.ReactElement {
  // Controller does not re-render a parent field when only one of its dotted
  // descendants changes. Subscribe explicitly so raw/object editors always
  // receive the same current value as their structured children.
  const value = useWatch({ control: surface.form.control, name });
  return <Controller
    key={name}
    control={surface.form.control}
    name={name}
    render={({ field, fieldState }) => children({
      value,
      error: fieldState.error?.message,
      messages: fieldErrorMessages(fieldState.error ? [fieldState.error] : [], ""),
      controlRef: field.ref,
      onCommit: () => surface.commitFieldInteraction(name),
      onChange: (value) => {
        surface.startFieldInteraction(name);
        surface.clearServerFieldError(name);
        field.onChange(value);
      },
    })}
  />;
}

/** Subscribe a custom record panel to FormView's single RHF value tree.
 * @owner FormView
 */
export function useFormViewValues<T = FormValues>(form: FormViewSaveSurface, path?: string): T {
  const watched = useWatch({ control: form.form.control, name: path as string });
  return watched as T;
}

/** Bind one descriptor to a nested path in FormView's existing RHF tree. Its control is read-only
 * where the form's `fieldReadOnly` says so for that path; declare a field for the path, hidden if
 * need be, to let the form's own save write it.
 * @kind surface
 */
export function BoundDescriptorField({
  form: surface,
  field,
  resource,
  scope = "",
  readOnly = false,
}: BoundDescriptorFieldProps): React.ReactElement {
  const modelMetadata = useModelMetadata(resource);
  const schemaMetadata = useSchemaFieldMetadata();
  const declared = React.useMemo(
    () => fieldsWithMetadataDefaults([field], modelMetadata, schemaMetadata)[0] ?? field,
    [field, modelMetadata, schemaMetadata],
  ) as DescriptorField;
  const scopedValues = useWatch({ control: surface.form.control, name: (scope || undefined) as string });
  const siblingValues = isFormValues(scopedValues) ? scopedValues : {};
  const resolved = resolveField(declared, siblingValues);
  const name = scope ? `${scope}.${resolved.name}` : resolved.name;
  if (!isFieldVisible(resolved, siblingValues)) return <></>;
  // The record form's own descriptor for the path, the one its save reads, carries the mode lock.
  const recordField = { ...(surface.recordField(name) ?? resolved), name };
  const fieldReadOnly = readOnly || surface.fieldReadOnly(recordField);
  const repair = surface.repairState(recordField);

  return <BoundFormValue form={surface} name={name}>{(bound) => <UnregisteredImplementationField
    field={resolved.name} marked={isUnregisteredImplementationField(surface.modelMetadata, surface.displayRecord, name, bound.value)}
    readOnly={fieldReadOnly} once={repair.repairing} refusal={repair.refusal}
  ><LabeledDescriptorField
    field={resolved}
    loading={repair.loading}
    value={bound.value}
    dialogValues={siblingValues}
    readOnly={fieldReadOnly}
    controlRef={bound.controlRef}
    messages={bound.messages}
    onCommit={bound.onCommit}
    onChange={(next) => { bound.onChange(next); surface.afterFieldChange(resolved, next, scope); }}
  /></UnregisteredImplementationField>}</BoundFormValue>;
}


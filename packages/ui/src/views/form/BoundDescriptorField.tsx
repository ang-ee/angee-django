import * as React from "react";
import { Controller, get, useWatch } from "react-hook-form";
import { useModelMetadata } from "@angee/metadata";

import type { MutationDialogField } from "./MutationDialog";
import { LabeledDescriptorField } from "./MutationDialog";
import { fieldErrorMessages, isFieldVisible, resolveField, type FormValues } from "./form-view-model";
import type { FormViewSaveSurface } from "./use-form-view-save";
import { fieldsWithMetadataDefaults } from "../resource/model-metadata-defaults";
import type { WidgetFocusTarget } from "../../widgets";

export interface BoundDescriptorFieldProps {
  /** The shared FormView surface that owns values, errors, dirtiness and save. */
  form: FormViewSaveSurface;
  /** Resource whose metadata owns the descriptor. */
  resource: string;
  /** Descriptor expressed relative to `scope`. */
  field: MutationDialogField;
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

/** Bind a domain-owned controlled editor to one value in FormView's RHF tree. */
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
      messages: fieldErrorMessages(fieldState.error ? [fieldState.error] : []),
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

/** Subscribe a custom record panel to FormView's single RHF value tree. */
export function useFormViewValues(form: FormViewSaveSurface): FormValues {
  return useWatch({ control: form.form.control }) as FormValues;
}

/** Bind one descriptor to a nested path in FormView's existing RHF tree. */
export function BoundDescriptorField({
  form: surface,
  field,
  resource,
  scope = "",
  readOnly = false,
}: BoundDescriptorFieldProps): React.ReactElement {
  const modelMetadata = useModelMetadata(resource);
  const declared = React.useMemo(
    () => fieldsWithMetadataDefaults([field], modelMetadata)[0] ?? field,
    [field, modelMetadata],
  ) as MutationDialogField;
  const watched = useWatch({ control: surface.form.control });
  const scopedValues = scope ? get(watched, scope) : watched;
  const siblingValues = isFormValues(scopedValues) ? scopedValues : {};
  const resolved = resolveField(declared, siblingValues) as MutationDialogField;
  const name = scope ? `${scope}.${resolved.name}` : resolved.name;
  if (!isFieldVisible(resolved, siblingValues)) return <></>;

  return <BoundFormValue form={surface} name={name}>{(bound) => <LabeledDescriptorField
    field={resolved}
    value={bound.value}
    dialogValues={siblingValues}
    readOnly={readOnly || surface.fieldReadOnly(resolved)}
    controlRef={bound.controlRef}
    messages={bound.messages}
    onCommit={bound.onCommit}
    onChange={(next) => { bound.onChange(next); surface.afterFieldChange(resolved, next, scope); }}
  />}</BoundFormValue>;
}

function isFormValues(value: unknown): value is FormValues {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

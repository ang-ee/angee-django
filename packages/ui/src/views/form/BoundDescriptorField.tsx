import * as React from "react";
import { Controller, get, useWatch } from "react-hook-form";
import { useModelMetadata } from "@angee/metadata";

import type { MutationDialogField } from "./MutationDialog";
import { LabeledDescriptorField } from "./MutationDialog";
import { fieldErrorMessages, isFieldVisible, resolveField, type FormValues } from "./form-view-model";
import type { FormViewSaveSurface } from "./use-form-view-save";
import { fieldsWithMetadataDefaults } from "../resource/model-metadata-defaults";

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

  return (
    <Controller
      key={name}
      control={surface.form.control}
      name={name}
      render={({ field: controller, fieldState }) => (
        <LabeledDescriptorField
          field={resolved}
          value={controller.value}
          dialogValues={siblingValues}
          readOnly={readOnly || surface.fieldReadOnly(resolved)}
          controlRef={controller.ref}
          messages={fieldErrorMessages(fieldState.error ? [fieldState.error] : [])}
          onCommit={() => surface.commitFieldInteraction(name)}
          onChange={(next) => {
            surface.startFieldInteraction(name);
            surface.clearServerFieldError(name);
            controller.onChange(next);
            surface.afterFieldChange(resolved, next, scope);
          }}
        />
      )}
    />
  );
}

function isFormValues(value: unknown): value is FormValues {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

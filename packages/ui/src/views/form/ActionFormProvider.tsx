import { createContext, useContext, useState, type ReactElement, type ReactNode } from "react";
import { FormProvider, type FieldValues } from "react-hook-form";

import { createPresetMemory, type PresetMemory } from "./form-view-model";
import type { FieldValidationForm, FieldValidationMethods } from "./use-field-validation";

const FieldValidationContext = createContext<FieldValidationMethods["registerFieldValidation"] | null>(null);
const PresetMemoryContext = createContext<PresetMemory | null>(null);

/** Native RHF state with the shared controlled-editor validation extension and the form's preset memory.
 * The memory lives exactly as long as this provider: a dialog mounts a fresh one for each session.
 * An owner that discards its form while the provider stays mounted clears the memory itself, as the
 * record form does on discard.
 * @kind null
 * @reason Provider infrastructure: ActionForm context.
 */
export function ActionFormProvider<TValues extends FieldValues, TContext = unknown, TOutput extends FieldValues = TValues>({
  children, registerFieldValidation, ...form
}: FieldValidationForm<TValues, TContext, TOutput> & { children: ReactNode }): ReactElement {
  const [presetMemory] = useState(createPresetMemory);
  return <FieldValidationContext.Provider value={registerFieldValidation}>
    <PresetMemoryContext.Provider value={presetMemory}>
      <FormProvider {...form}>{children}</FormProvider>
    </PresetMemoryContext.Provider>
  </FieldValidationContext.Provider>;
}

export function useFieldValidationRegistration(): FieldValidationMethods["registerFieldValidation"] | null {
  return useContext(FieldValidationContext);
}

/** The preset memory of the enclosing ActionFormProvider's form, one per provider (see `seedPreset`). */
export function usePresetMemory(): PresetMemory | null {
  return useContext(PresetMemoryContext);
}

import { createContext, useContext, type ReactElement, type ReactNode } from "react";
import { FormProvider, type FieldValues } from "react-hook-form";

import type { FieldValidationForm, FieldValidationMethods } from "./use-field-validation";

const FieldValidationContext = createContext<FieldValidationMethods["registerFieldValidation"] | null>(null);

/** Native RHF state with the shared controlled-editor validation extension. */
export function ActionFormProvider<TValues extends FieldValues, TContext = unknown, TOutput extends FieldValues = TValues>({
  children, registerFieldValidation, ...form
}: FieldValidationForm<TValues, TContext, TOutput> & { children: ReactNode }): ReactElement {
  return <FieldValidationContext.Provider value={registerFieldValidation}>
    <FormProvider {...form}>{children}</FormProvider>
  </FieldValidationContext.Provider>;
}

export function useFieldValidationRegistration(): FieldValidationMethods["registerFieldValidation"] | null {
  return useContext(FieldValidationContext);
}

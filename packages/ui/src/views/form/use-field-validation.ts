import { useCallback, useRef } from "react";
import { get, type ErrorOption, type FieldValues, type UseFormReturn } from "react-hook-form";

export interface FieldValidationMethods {
  /** Register validation owned by a mounted controlled editor. */
  registerFieldValidation: (
    name: string,
    validate: (value: unknown, values: Record<string, unknown>) => string | undefined,
  ) => () => void;
}

/** ActionFormProvider composes this extension with native RHF methods. */
export type FieldValidationForm<TValues extends FieldValues, TContext = unknown, TOutput extends FieldValues = TValues> =
  UseFormReturn<TValues, TContext, TOutput> & FieldValidationMethods;

/** One registry for editor validation that complements either RHF validation mode. */
export function useFieldValidation() {
  const validators = useRef(new Map<string, Set<Parameters<FieldValidationMethods["registerFieldValidation"]>[1]>>());
  const registerFieldValidation = useCallback<FieldValidationMethods["registerFieldValidation"]>((name, validate) => {
    const fieldValidators = validators.current.get(name) ?? new Set();
    fieldValidators.add(validate);
    validators.current.set(name, fieldValidators);
    return () => {
      fieldValidators.delete(validate);
      if (!fieldValidators.size) validators.current.delete(name);
    };
  }, []);
  const validateFields = useCallback((
    values: Record<string, unknown>,
    report: (name: string, error: ErrorOption) => void,
  ): boolean => {
    let invalid = false;
    for (const [name, fieldValidators] of validators.current) {
      for (const validate of fieldValidators) {
        const message = validate(get(values, name), values);
        if (message) { report(name, { type: "composed", message }); invalid = true; break; }
      }
    }
    return invalid;
  }, []);
  return { registerFieldValidation, validateFields };
}

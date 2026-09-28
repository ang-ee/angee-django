import Ajv2020 from "ajv/dist/2020.js";
import type { AnySchema, ErrorObject, Options } from "ajv";
import addFormats from "ajv-formats";
import { get, set, type FieldErrors, type FieldValues, type Resolver } from "react-hook-form";

import { FORM_SPEC_ANNOTATIONS } from "./form-spec-schema";
import { applyFormErrors, invalidFormSubmit, type ValidationErrors } from "./validation-errors";

/** Draft 2020-12 with native formats, discriminator selection and FormSpec annotations. */
export function createJsonSchemaAjv(options: Options = {}): Ajv2020 {
  const ajv = new Ajv2020({
    strict: true, coerceTypes: false, useDefaults: false, removeAdditional: false,
    ...options, allErrors: true, discriminator: true,
  });
  addFormats(ajv, { mode: "full" });
  for (const keyword of FORM_SPEC_ANNOTATIONS) {
    if (!ajv.getKeyword(keyword)) ajv.addKeyword(keyword);
  }
  return ajv;
}

/** Translate native JSON Pointer locations into the form owner's dotted issue paths. */
export function ajvValidationErrors(errors: readonly ErrorObject<string, Record<string, unknown>>[] | null | undefined): ValidationErrors {
  const fieldErrors: Record<string, string[]> = Object.create(null);
  const formErrors: string[] = [];
  for (const error of errors ?? []) {
    const segments = error.instancePath.split("/").slice(1)
      .map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"));
    const property = error.keyword === "required" ? error.params.missingProperty
      : error.keyword === "additionalProperties" ? error.params.additionalProperty
      : error.keyword === "discriminator" ? error.params.tag : undefined;
    if (typeof property === "string") segments.push(property);
    const path = segments.join(".");
    const message = error.message ?? error.keyword;
    if (path) (fieldErrors[path] ??= []).push(message);
    else formErrors.push(message);
  }
  return { fieldErrors, formErrors };
}

/**
 * Compile once, outside render or in useMemo. The native Ajv instance may carry
 * registered references or coercion options. Validation returns cloned values
 * so coercion/defaults never rewrite the collecting form's draft.
 */
export function createJsonSchemaResolver<
  TValues extends FieldValues = Record<string, unknown>,
  TContext = unknown,
  TOutput extends FieldValues = TValues,
>(schema: AnySchema, ajv: Ajv2020 = createJsonSchemaAjv()): Resolver<TValues, TContext, TOutput> {
  const validate = ajv.compile<TOutput>(schema);
  if ("$async" in validate) throw new Error("JSON Schema form resolvers require a synchronous schema.");
  return (values) => {
    const candidate: unknown = structuredClone(values);
    if (validate(candidate)) return { values: candidate, errors: {} };
    const errors: FieldErrors<TValues> = {};
    applyFormErrors<TValues, never>({ setError: (name, error) => { set(errors, name, { ...get(errors, name), ...error }); } },
      invalidFormSubmit(ajvValidationErrors(validate.errors)));
    return { values: {}, errors };
  };
}

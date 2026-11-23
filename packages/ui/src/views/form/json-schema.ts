import type Ajv2020 from "ajv/dist/2020.js";
import type { AnySchema, ErrorObject } from "ajv";
import * as v from "valibot";
import { get, set, type FieldErrors, type FieldValues, type Resolver } from "react-hook-form";

import { createJsonSchemaAjv } from "./json-schema-validator";

import type { UiTranslate } from "../../i18n";
import { enUiBundle } from "../../i18n/en";
import { createAngeeI18nInstance } from "../../runtime/i18n";
import type { WidgetMap } from "../../runtime/contracts";
import { JsonValueSchema } from "../../widgets/json-value";
import type { ActionFormDefinition } from "../page/Action";
import { deserializeFormSpec, formSpecInitialValues, normalizeFormSpecValues, type FormSpecFieldDescriptor } from "./form-spec";
import { parseFormSpec, parseFormSpecPayload } from "./form-spec-schema";
import { applyFormErrors, invalidFormSubmit, jsonSchemaErrorMessage, type ValidationErrors } from "./validation-errors";

// The bundled English, so a counted message picks its plural form natively.
const translateDefault: UiTranslate = createAngeeI18nInstance(enUiBundle).getFixedT(null, "ui");

export { createJsonSchemaAjv } from "./json-schema-validator";

/** A caller-owned message, with native keyword parameters available for translation. */
export type JsonSchemaErrorMessage = (error: ErrorObject<string, Record<string, unknown>>) => string;

/** Translate native JSON Pointer locations into the form owner's dotted issue paths. */
export function ajvValidationErrors(
  errors: readonly ErrorObject<string, Record<string, unknown>>[] | null | undefined,
  messageForError?: JsonSchemaErrorMessage,
  translate: UiTranslate = translateDefault,
): ValidationErrors {
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
    const message = messageForError ? messageForError(error) : jsonSchemaErrorMessage(error, translate);
    if (path) (fieldErrors[path] ??= []).push(message);
    else formErrors.push(message);
  }
  return { fieldErrors, formErrors };
}

/**
 * Compile once, outside render or in useMemo. The native Ajv instance may carry
 * registered references or coercion options. Validation returns cloned values
 * so coercion/defaults never rewrite the collecting form's draft.
 * The default messages use the UI namespace; pass `useUiT()` as `translate`
 * for the active locale. `messageForError` can override native keyword messages
 * without replacing validation or issue paths.
 */
export function createJsonSchemaResolver<
  TValues extends FieldValues = Record<string, unknown>,
  TContext = unknown,
  TOutput extends FieldValues = TValues,
>(
  schema: AnySchema,
  ajv: Ajv2020 = createJsonSchemaAjv(),
  messageForError?: JsonSchemaErrorMessage,
  translate?: UiTranslate,
): Resolver<TValues, TContext, TOutput> {
  const validate = ajv.compile<TOutput>(schema);
  if ("$async" in validate) throw new Error("JSON Schema form resolvers require a synchronous schema.");
  return (values) => {
    const candidate: unknown = structuredClone(values);
    if (validate(candidate)) return { values: candidate, errors: {} };
    const errors: FieldErrors<TValues> = {};
    applyFormErrors<TValues, never>({ setError: (name, error) => { set(errors, name, Object.assign({}, get(errors, name), error)); } },
      invalidFormSubmit(ajvValidationErrors(validate.errors, messageForError, translate)));
    return { values: {}, errors };
  };
}

/**
 * Project a frozen JSON Schema into an action's native descriptor form. Schema
 * branches use their declared discriminator; DescriptorFieldList owns branch
 * reset, FormSpec owns values/widgets, and Ajv validates only the selected branch.
 * Keep this optional import outside the main UI entry so ordinary actions do not
 * load Ajv. Call once for each dialog opening, typically from `Action.args`.
 */
export function jsonSchemaActionArgs(
  value: unknown,
  widgets: WidgetMap,
  options: { initialValues?: unknown; translate?: UiTranslate } = {},
): ActionFormDefinition {
  const schema = v.parse(v.record(v.string(), JsonValueSchema), value);
  const spec = parseFormSpec(schema);
  const discriminator = spec.discriminator?.propertyName;
  const branches = new Map<unknown, readonly FormSpecFieldDescriptor[]>();
  if (spec.oneOf) {
    if (!discriminator || !spec.properties?.[discriminator]) {
      throw new Error("A schema action with oneOf requires a declared discriminator field.");
    }
    for (const branch of spec.oneOf) {
      const { [discriminator]: tag, ...properties } = branch.properties ?? {};
      const tags = tag?.const !== undefined ? [tag.const] : tag?.enum;
      if (!tags?.length || tags.some((item) => typeof item !== "string")) {
        throw new Error("Each schema action branch requires a string discriminator constant or enum.");
      }
      const fields = deserializeFormSpec({
        ...spec, ...branch, properties,
        propertyOrder: branch.propertyOrder?.filter((name) => name !== discriminator),
        required: branch.required?.filter((name) => name !== discriminator),
      }, widgets);
      for (const tag of tags) {
        if (branches.has(tag)) throw new Error(`Duplicate schema action discriminator: ${String(tag)}.`);
        branches.set(tag, fields.map((field) => ({ ...field, controlKey: String(tag) })));
      }
    }
  }
  const rootFields = deserializeFormSpec(spec, widgets).map((field) => field.name === discriminator
    ? { ...field, branchReset: (value: unknown) => {
      const fields = branches.get(value) ?? [];
      return { fields: fields.map((entry) => entry.name), values: formSpecInitialValues(fields, {}) };
    } } : field);
  const fields = (values: Record<string, unknown>) => [
    ...rootFields, ...(discriminator ? branches.get(values[discriminator]) ?? [] : []),
  ];
  const initialValues = parseFormSpecPayload(options.initialValues);
  if (discriminator && initialValues[discriminator] === undefined) {
    const field = rootFields.find((entry) => entry.name === discriminator);
    const declared = spec.properties?.[discriminator];
    initialValues[discriminator] = field?.defaultValue ?? declared?.options?.[0]?.value
      ?? declared?.enum?.[0] ?? branches.keys().next().value;
  }
  const validate = createJsonSchemaResolver(schema, createJsonSchemaAjv({ strictTuples: false }), undefined, options.translate);
  return {
    fields,
    defaultValues: formSpecInitialValues(fields(initialValues), initialValues),
    resolver: (values, context, settings) => validate(normalizeFormSpecValues(fields(values), values), context, settings),
    fieldNames: [...new Set([...rootFields, ...[...branches.values()].flat()].map((field) => field.name))],
  };
}

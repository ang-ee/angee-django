import Ajv2020 from "ajv/dist/2020.js";
import type { AnySchema, ErrorObject, Options } from "ajv";
import addFormats from "ajv-formats";
import { get, set, type FieldErrors, type FieldValues, type Resolver } from "react-hook-form";

import type { UiTranslate } from "../../i18n";
import { enUiMessages } from "../../i18n/en";
import { createAngeeI18nInstance } from "../../runtime/i18n";
import { FORM_SPEC_ANNOTATIONS } from "./form-spec-schema";
import { applyFormErrors, invalidFormSubmit, type ValidationErrors } from "./validation-errors";

const fallbackT = createAngeeI18nInstance({}).getFixedT(null, "ui");
const translateDefault: UiTranslate = (key, vars) => fallbackT(key, { ...vars, defaultValue: enUiMessages[key] ?? key });

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

/** A caller-owned message, with native keyword parameters available for translation. */
export type JsonSchemaErrorMessage = (error: ErrorObject<string, Record<string, unknown>>) => string;

function typeLabel(value: unknown, t: UiTranslate): string | undefined {
  switch (value) {
    case "string": return t("form.validation.type.text");
    case "number": return t("form.validation.type.number");
    case "integer": return t("form.validation.type.integer");
    case "boolean": return t("form.validation.type.boolean");
    case "array": return t("form.validation.type.array");
    case "object": return t("form.validation.type.object");
    case "null": return t("form.validation.type.null");
    default: return undefined;
  }
}

function formatLabel(value: unknown, t: UiTranslate): string | undefined {
  switch (value) {
    case "date": return t("form.validation.format.date");
    case "time": case "iso-time": return t("form.validation.format.time");
    case "date-time": case "iso-date-time": return t("form.validation.format.dateTime");
    case "duration": return t("form.validation.format.duration");
    case "email": return t("form.validation.format.email");
    case "hostname": return t("form.validation.format.hostname");
    case "ipv4": case "ipv6": return t("form.validation.format.ipAddress");
    case "uri": case "uri-reference": case "url": return t("form.validation.format.address");
    case "uri-template": return t("form.validation.format.addressTemplate");
    case "uuid": return t("form.validation.format.identifier");
    case "regex": return t("form.validation.format.expression");
    case "json-pointer": case "relative-json-pointer": return t("form.validation.format.jsonPointer");
    default: return undefined;
  }
}

/** The shared UI copy for native Ajv issues; the caller supplies its active UI translator. */
function jsonSchemaErrorMessage({ keyword, params }: Parameters<JsonSchemaErrorMessage>[0], t: UiTranslate): string {
  switch (keyword) {
    case "required": return t("form.required");
    case "enum": case "discriminator": return t("form.validation.choice");
    case "type": {
      const type = typeLabel(params.type, t);
      return type ? t("form.validation.type", { type }) : t("form.invalidValue");
    }
    case "minLength": return t("form.validation.minLength", { limit: Number(params.limit) });
    case "maxLength": return t("form.validation.maxLength", { limit: Number(params.limit) });
    case "minimum": return t("form.validation.minimum", { limit: Number(params.limit) });
    case "maximum": return t("form.validation.maximum", { limit: Number(params.limit) });
    case "exclusiveMinimum": return t("form.validation.exclusiveMinimum", { limit: Number(params.limit) });
    case "exclusiveMaximum": return t("form.validation.exclusiveMaximum", { limit: Number(params.limit) });
    case "minItems": return t("form.validation.minItems", { limit: Number(params.limit) });
    case "maxItems": return t("form.validation.maxItems", { limit: Number(params.limit) });
    case "uniqueItems": return t("form.validation.uniqueItems");
    case "format": {
      const format = formatLabel(params.format, t);
      return format ? t("form.validation.format", { format }) : t("form.validation.pattern");
    }
    case "pattern": return t("form.validation.pattern");
    case "const": return t("form.validation.const");
    case "additionalProperties": return t("form.validation.additionalProperties");
    default: return t("form.invalidValue");
  }
}

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
    applyFormErrors<TValues, never>({ setError: (name, error) => { set(errors, name, { ...get(errors, name), ...error }); } },
      invalidFormSubmit(ajvValidationErrors(validate.errors, messageForError, translate)));
    return { values: {}, errors };
  };
}

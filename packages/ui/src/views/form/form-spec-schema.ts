import * as v from "valibot";
import type { CrudFilter } from "@refinedev/core";

import { JsonValueSchema } from "../../widgets/json-value";

const NonEmptyString = v.pipe(v.string(), v.minLength(1));
const FieldTypeSchema = v.picklist(["string", "integer", "number", "boolean", "object", "array", "any"]);
const JsonFieldScalarTypeSchema = v.union([FieldTypeSchema, v.literal("null")]);
const JsonFieldTypeSchema = v.union([
  JsonFieldScalarTypeSchema,
  v.array(v.picklist(["string", "integer", "number", "boolean", "object", "array", "null"])),
]);
const FieldLayoutSchema = v.picklist(["context", "input"]);
export type FormSpecFieldType = v.InferOutput<typeof FieldTypeSchema>;

const FilterSchema: v.GenericSchema<unknown, CrudFilter> = v.lazy(() => v.variant("operator", [
  v.object({
    operator: v.picklist(["and", "or"]),
    key: v.optional(v.string()),
    value: v.array(FilterSchema),
  }),
  v.object({
    operator: v.picklist([
      "eq", "ne", "eqs", "nes", "lt", "gt", "lte", "gte", "in", "nin", "ina", "nina",
      "contains", "ncontains", "containss", "ncontainss", "between", "nbetween", "null", "nnull",
      "startswith", "nstartswith", "startswiths", "nstartswiths", "endswith", "nendswith", "endswiths", "nendswiths",
    ]),
    field: NonEmptyString,
    value: v.union([JsonValueSchema, v.undefined()]),
  }),
], (issue) => `unknown Refine CRUD operator "${String(issue.input)}".`));
const RelationSchema = v.object({
  resource: NonEmptyString,
  permission: v.optional(v.pipe(
    NonEmptyString,
    v.regex(/^[a-z][a-z0-9_]*$/, "relation permission must be a policy identifier"),
  )),
  labelField: v.optional(NonEmptyString),
  filters: v.optional(v.array(FilterSchema)),
  create: v.optional(v.object({
    resource: NonEmptyString,
    defaultValues: v.optional(v.record(v.string(), JsonValueSchema)),
    actionLabel: v.optional(NonEmptyString),
    title: v.optional(NonEmptyString),
  })),
});
const FieldBaseSchema = v.object({
  type: v.optional(JsonFieldTypeSchema),
  required: v.optional(v.array(v.string())),
  propertyOrder: v.optional(v.array(NonEmptyString)),
  widget: v.optional(NonEmptyString),
  label: v.optional(NonEmptyString),
  addLabel: v.optional(NonEmptyString),
  removeLabel: v.optional(NonEmptyString),
  description: v.optional(NonEmptyString),
  placeholder: v.optional(NonEmptyString),
  readOnly: v.optional(v.boolean()),
  hidden: v.optional(v.boolean()),
  layout: v.optional(FieldLayoutSchema),
  nullable: v.optional(v.boolean()),
  omittable: v.optional(v.boolean()),
  presenceRequired: v.optional(v.boolean()),
  minimum: v.optional(v.pipe(v.number(), v.finite())),
  maximum: v.optional(v.pipe(v.number(), v.finite())),
  minLength: v.optional(v.pipe(v.number(), v.integer(), v.minValue(0))),
  maxLength: v.optional(v.pipe(v.number(), v.integer(), v.minValue(0))),
  minItems: v.optional(v.pipe(v.number(), v.integer(), v.minValue(0))),
  maxItems: v.optional(v.pipe(v.number(), v.integer(), v.minValue(0))),
  defaultValue: v.optional(JsonValueSchema),
  default: v.optional(JsonValueSchema),
  const: v.optional(JsonValueSchema),
  format: v.optional(NonEmptyString),
  pattern: v.optional(v.string()),
  enum: v.optional(v.array(v.string("form-spec select values must be strings."))),
  // Option annotations are an extension seam. The generic form owner consumes
  // only value/label/disabled and preserves domain annotations for its caller.
  options: v.optional(v.array(v.looseObject({
    // JSON Pointer uses the empty string for the root document. It is a valid
    // authored choice value even though human-facing option labels stay
    // non-empty.
    value: v.string(),
    label: NonEmptyString,
    disabled: v.optional(v.boolean()),
  }))),
  relation: v.optional(RelationSchema),
});
/** Only recursive edges need an annotation; scalar facts are inferred. */
export type FormSpecWire = v.InferOutput<typeof FieldBaseSchema> & {
  properties?: Record<string, FormSpecWire>;
  items?: FormSpecWire;
  oneOf?: FormSpecWire[];
  anyOf?: FormSpecWire[];
  allOf?: FormSpecWire[];
  if?: FormSpecWire;
  then?: FormSpecWire;
  else?: FormSpecWire;
  not?: FormSpecWire;
  $defs?: Record<string, FormSpecWire>;
  definitions?: Record<string, FormSpecWire>;
  $ref?: string;
  additionalProperties?: boolean | FormSpecWire;
};
const FieldSchema: v.GenericSchema<unknown, FormSpecWire> = v.lazy(() => v.object({
  ...FieldBaseSchema.entries,
  properties: v.optional(v.record(v.string(), FieldSchema)),
  items: v.optional(FieldSchema),
  oneOf: v.optional(v.array(FieldSchema)),
  anyOf: v.optional(v.array(FieldSchema)),
  allOf: v.optional(v.array(FieldSchema)),
  if: v.optional(FieldSchema),
  then: v.optional(FieldSchema),
  else: v.optional(FieldSchema),
  not: v.optional(FieldSchema),
  $defs: v.optional(v.record(v.string(), FieldSchema)),
  definitions: v.optional(v.record(v.string(), FieldSchema)),
  $ref: v.optional(v.string()),
  additionalProperties: v.optional(v.union([v.boolean(), FieldSchema])),
}));
const FormSchema = v.pipe(FieldSchema, v.check(
  (schema) => schema.type === undefined || schema.type === "object",
  'root type must be "object".',
));

/** Validate the supported emitted form vocabulary before resolving widgets. */
export function parseFormSpec(value: unknown): FormSpecWire {
  const result = v.safeParse(FormSchema, value);
  if (result.success) return result.output;
  const issue = result.issues[0];
  const path = v.getDotPath(issue)?.replace(/(^|\.)properties\./g, "$1") ?? "form spec";
  throw new Error(`Invalid ${path}: ${issue.message}`);
}

/** Invalid/non-object payloads carry no seeds; only declared fields consume them. */
export function parseFormSpecPayload(payload: unknown): Record<string, unknown> {
  const result = v.safeParse(v.record(v.string(), v.unknown()), payload);
  return result.success ? result.output : {};
}

const JSON_SCHEMA_KEYWORDS = new Set([
  "type", "required", "description", "readOnly", "nullable", "minimum", "maximum",
  "minLength", "maxLength", "minItems", "maxItems", "default", "const", "format", "pattern", "enum",
]);

/** Every non-standard field entry is an annotation for full JSON Schema validators. */
export const FORM_SPEC_ANNOTATIONS: readonly string[] = Object.keys(FieldBaseSchema.entries)
  .filter((keyword) => !JSON_SCHEMA_KEYWORDS.has(keyword));

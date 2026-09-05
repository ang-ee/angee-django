import * as v from "valibot";
import type { CrudFilter } from "@refinedev/core";

const NonEmptyString = v.pipe(v.string(), v.minLength(1));
const FieldTypeSchema = v.picklist(["string", "integer", "number", "boolean", "object", "array", "any"]);
export type FormSpecFieldType = v.InferOutput<typeof FieldTypeSchema>;

type JsonValue = null | string | number | boolean | JsonValue[] | { [key: string]: JsonValue };
// Wire validators consume unknown input. Using the recursive output as input
// also expands Valibot optional-default inference past TypeScript's declaration limit.
const JsonSchema: v.GenericSchema<unknown, JsonValue> = v.lazy(() => v.union([
  v.null(), v.string(), v.pipe(v.number(), v.finite()), v.boolean(),
  v.array(JsonSchema), v.record(v.string(), JsonSchema),
]));
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
    value: v.union([JsonSchema, v.undefined()]),
  }),
], (issue) => `unknown Refine CRUD operator "${String(issue.input)}".`));
const RelationSchema = v.object({
  resource: NonEmptyString,
  labelField: v.optional(NonEmptyString),
  filters: v.optional(v.array(FilterSchema)),
  create: v.optional(v.object({ resource: NonEmptyString })),
});
const FieldBaseSchema = v.object({
  type: v.optional(FieldTypeSchema),
  required: v.optional(v.array(v.string())),
  widget: v.optional(NonEmptyString),
  label: v.optional(NonEmptyString),
  description: v.optional(NonEmptyString),
  placeholder: v.optional(NonEmptyString),
  readOnly: v.optional(v.boolean()),
  defaultValue: v.optional(JsonSchema),
  const: v.optional(JsonSchema),
  enum: v.optional(v.array(v.string("form-spec select values must be strings."))),
  options: v.optional(v.array(v.object({
    value: NonEmptyString,
    label: NonEmptyString,
    disabled: v.optional(v.boolean()),
  }))),
  relation: v.optional(RelationSchema),
});
/** Only recursive edges need an annotation; scalar facts are inferred. */
export type FormSpecWire = v.InferOutput<typeof FieldBaseSchema> & {
  properties?: Record<string, FormSpecWire>;
  items?: FormSpecWire;
};
const FieldSchema: v.GenericSchema<unknown, FormSpecWire> = v.lazy(() => v.object({
  ...FieldBaseSchema.entries,
  properties: v.optional(v.record(v.string(), FieldSchema)),
  items: v.optional(FieldSchema),
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

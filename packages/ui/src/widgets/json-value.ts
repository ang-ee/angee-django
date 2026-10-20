import * as v from "valibot";

export type JsonObject = { readonly [key: string]: JsonValue };

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | readonly JsonValue[]
  | JsonObject;

function isPlainObject(input: unknown): input is Record<string, unknown> {
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    return false;
  }
  const prototype = Object.getPrototypeOf(input);
  return prototype === Object.prototype || prototype === null;
}

/** Recursive JSON data contract shared by form specs, widgets, and transports.
 * @kind null
 * @reason Schema infrastructure: validates serialized runtime contracts.
 */
export const JsonValueSchema: v.GenericSchema<unknown, JsonValue> = v.lazy(() =>
  v.union([
    v.null(),
    v.string(),
    v.pipe(v.number(), v.finite()),
    v.boolean(),
    v.array(JsonValueSchema),
    v.pipe(
      v.custom<Record<string, unknown>>(isPlainObject),
      v.record(v.string(), JsonValueSchema),
    ),
  ]),
);

/** Validate an optional untyped form value at the shared JSON boundary.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function jsonValueFromUnknown(value: unknown): JsonValue | undefined {
  if (value === undefined) return undefined;
  const result = v.safeParse(JsonValueSchema, omitUndefinedObjectProperties(value));
  return result.success ? result.output : undefined;
}

/** Validate an untyped value and retain it only when its JSON root is an object.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function jsonObjectFromUnknown(value: unknown): JsonObject | undefined {
  const json = jsonValueFromUnknown(value);
  return isJsonObject(json) ? json : undefined;
}

/** Narrow an already validated JSON value to an object root.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function isJsonObject(value: JsonValue | undefined): value is JsonObject {
  return isPlainObject(value);
}

function omitUndefinedObjectProperties(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(omitUndefinedObjectProperties);
  }
  if (!isPlainObject(value)) return value;
  return Object.fromEntries(
    Object.entries(value)
      .filter(([, entry]) => entry !== undefined)
      .map(([key, entry]) => [key, omitUndefinedObjectProperties(entry)]),
  );
}

import type { FieldDescriptor } from "../page";
import type { JsonSchemaRule } from "./validation-errors";

/** Normalize scalar text without interpreting domain-specific objects.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function textValue(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
}

/** Return the native empty value for one descriptor field. */
export function emptyValueForField(field: Pick<FieldDescriptor, "widget" | "kind">): unknown {
  if (field.kind === "array" || field.widget === "tagInput") return [];
  if (field.kind === "object") return {};
  if (field.kind === "boolean" || field.kind === "switch" || field.widget === "switch") return false;
  if (field.kind === "integer" || field.kind === "number" || field.kind === "any") {
    return field.widget === "select" ? "" : null;
  }
  return "";
}

/** The rule a field's value fails at one dotted form path, in the keyword and parameters `jsonSchemaErrorMessage` reads. */
export interface FieldRuleError extends JsonSchemaRule {
  path: string;
}

/** The rule a missing value fails: `required`, which carries no parameters; frozen, and typed read-only. */
export const REQUIRED_RULE: Readonly<{ keyword: "required"; params: Readonly<Record<string, never>> }> =
  Object.freeze({ keyword: "required", params: Object.freeze({}) });

/**
 * The first rule `value` fails: `required` when it is absent or a `null` the field does not
 * allow; then, unless `validateConstraints` is off and a schema resolver reports the rest, the
 * descriptor's constraints.
 */
function failedValueRule(
  field: FieldDescriptor,
  value: unknown,
  present: boolean,
  validateConstraints: boolean,
): JsonSchemaRule | undefined {
  if (!present || value === undefined || (value === null && !field.nullable)) return REQUIRED_RULE;
  if (value === null || !validateConstraints) return undefined;
  return failedDescriptorConstraint(field, value);
}

/**
 * The first descriptor constraint a present, non-null value fails: an integer or number field's
 * `type`, then the lower and the upper bound of its size, `minLength`/`maxLength` for text,
 * `minimum`/`maximum` for a number, `minItems`/`maxItems` for a list. An empty text under a
 * `minLength` of one in a field that is not omittable is a missing value: `required`; an
 * omittable field keeps the `minLength` rule.
 */
function failedDescriptorConstraint(field: FieldDescriptor, value: unknown): JsonSchemaRule | undefined {
  if ((field.kind === "integer" || field.kind === "number") && typeof value !== "number") {
    return { keyword: "type", params: { type: field.kind } };
  }
  const bounds = typeof value === "string" ? { size: value.length, lower: "minLength", upper: "maxLength" } as const
    : typeof value === "number" ? { size: value, lower: "minimum", upper: "maximum" } as const
    : Array.isArray(value) ? { size: value.length, lower: "minItems", upper: "maxItems" } as const
    : undefined;
  if (!bounds) return undefined;
  const min = field[bounds.lower];
  const max = field[bounds.upper];
  if (min !== undefined && bounds.size < min) {
    return bounds.lower === "minLength" && min === 1 && !field.omittable
      ? REQUIRED_RULE
      : { keyword: bounds.lower, params: { limit: min } };
  }
  if (max !== undefined && bounds.size > max) return { keyword: bounds.upper, params: { limit: max } };
  return undefined;
}

/** The rules a structured FormSpec value fails, one per dotted path, the field's path before its
 * members' and its list items'. A path reports the first rule it fails, in this order: presence
 * (`required`: absent, or `null` where the field is not nullable), then an integer or number
 * field's `type`, then the lower bound before the upper (`minLength`, `maxLength`, `minimum`,
 * `maximum`, `minItems`, `maxItems`), each with its `limit`. One case reads as presence: an
 * empty text under a `minLength` of one is `required` when the field is not omittable. With
 * `validateConstraints=false`, only presence is checked, at every depth.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function structuredFieldErrors(
  field: FieldDescriptor,
  value: unknown,
  present: boolean,
  path = field.name,
  validateConstraints = true,
): readonly FieldRuleError[] {
  const errors: FieldRuleError[] = [];
  const failed = field.presenceRequired || present
    ? failedValueRule(field, value, present, validateConstraints)
    : undefined;
  if (failed) errors.push({ path, ...failed });
  if (value && typeof value === "object" && !Array.isArray(value) && field.objectTemplate) {
    const record = value as Record<string, unknown>;
    for (const child of field.objectTemplate) {
      errors.push(...structuredFieldErrors(
        child,
        record[child.name],
        Object.hasOwn(record, child.name),
        `${path}.${child.name}`,
        validateConstraints,
      ));
    }
  }
  if (Array.isArray(value) && field.itemTemplate) {
    value.forEach((item, index) => {
      errors.push(...structuredFieldErrors(field.itemTemplate!, item, true, `${path}.${index}`, validateConstraints));
    });
  }
  return errors;
}

/** Whether a descriptor opts into JSON presence/constraint validation. */
export function isStructuredPresenceField(field: FieldDescriptor): boolean {
  return Boolean(
    field.presenceRequired || field.omittable || field.objectTemplate || field.itemTemplate
    || field.minLength !== undefined || field.maxLength !== undefined
    || field.minimum !== undefined || field.maximum !== undefined
    || field.minItems !== undefined || field.maxItems !== undefined
  );
}

/** Update one structured value without dropping retained sibling fields. */
export function updatedRecord(value: Readonly<Record<string, unknown>>, key: string, next: unknown): Record<string, unknown> {
  if (next !== undefined) return { ...value, [key]: next };
  const updated = { ...value };
  delete updated[key];
  return updated;
}

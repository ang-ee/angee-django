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
 * Whether `value` holds nothing a person entered: `null` or `undefined`, a text of blanks only, or
 * an empty list. Any other value is filled, `false`, `0` and an empty object included.
 */
export function isEmptyFieldValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return value.trim() === "";
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

/**
 * The first rule `value` fails. A value is missing when it is absent, `undefined`, or a `null` the
 * field does not allow; a missing value fails `required` where the field is `required`,
 * presence-required, or holds the key. Any other value is a value, a `null` the field allows, a
 * blank text and an empty list included: whether it is acceptable is the descriptor's
 * constraints' to say, unless `validateConstraints` is off and a schema resolver reports them.
 */
function failedValueRule(
  field: FieldDescriptor,
  value: unknown,
  present: boolean,
  required: boolean,
  validateConstraints: boolean,
): JsonSchemaRule | undefined {
  if (!present || value === undefined || (value === null && !field.nullable)) {
    return required || field.presenceRequired || present ? REQUIRED_RULE : undefined;
  }
  if (value === null || !validateConstraints) return undefined;
  return failedDescriptorConstraint(field, value);
}

/**
 * The first descriptor constraint a present, non-null value fails: an integer or number field's
 * `type`, then the lower and the upper bound of its size, `minLength`/`maxLength` for text,
 * `minimum`/`maximum` for a number, `minItems`/`maxItems` for a list. An empty text under a
 * `minLength` of one in a field that is neither omittable nor nullable is a missing value:
 * `required`; a field that may be left without a value keeps the `minLength` rule.
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
    return bounds.lower === "minLength" && min === 1 && !field.omittable && !field.nullable
      ? REQUIRED_RULE
      : { keyword: bounds.lower, params: { limit: min } };
  }
  if (max !== undefined && bounds.size > max) return { keyword: bounds.upper, params: { limit: max } };
  return undefined;
}

/** The rules a structured FormSpec value fails, one per dotted path, the field's path before its
 * members' and its list items'. A path reports the first rule it fails, in this order: presence
 * and `required`, then an integer or number field's `type`, then the lower bound before the upper
 * (`minLength`, `maxLength`, `minimum`, `maximum`, `minItems`, `maxItems`), each with its `limit`.
 * `required` means presence here: a missing value (absent, `undefined`, or a `null` the field does
 * not allow) fails `required` where the field is required, presence-required, or holds the key,
 * while a `null` it allows, a blank text, an empty list and any object are values, which the
 * descriptor's own constraints (`minLength`, `minItems`, …) judge. The field's own path is
 * required when `required` says so (by default its own flag); each member answers to its own
 * flag. One case reads as presence: an empty text under a `minLength` of one is `required` when
 * the field is neither omittable nor nullable. With `validateConstraints=false`, only presence is
 * checked, at every depth. Below the field's own path, a member or a list item that is `hidden` or
 * `readOnly` is not asked, nor anything under it: the object and list widgets (`StructuredField`)
 * render no control for a hidden member and a read-only control for a read-only one, so the person
 * cannot change it and the server answers for it.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function structuredFieldErrors(
  field: FieldDescriptor,
  value: unknown,
  present: boolean,
  path = field.name,
  validateConstraints = true,
  required = Boolean(field.required),
): readonly FieldRuleError[] {
  const errors: FieldRuleError[] = [];
  const failed = failedValueRule(field, value, present, required, validateConstraints);
  if (failed) errors.push({ path, ...failed });
  if (value && typeof value === "object" && !Array.isArray(value) && field.objectTemplate) {
    const record = value as Record<string, unknown>;
    for (const child of field.objectTemplate.filter(personChanges)) {
      errors.push(...structuredFieldErrors(
        child,
        record[child.name],
        Object.hasOwn(record, child.name),
        `${path}.${child.name}`,
        validateConstraints,
      ));
    }
  }
  if (Array.isArray(value) && field.itemTemplate && personChanges(field.itemTemplate)) {
    value.forEach((item, index) => {
      errors.push(...structuredFieldErrors(field.itemTemplate!, item, true, `${path}.${index}`, validateConstraints));
    });
  }
  return errors;
}

/** Whether a member or list item is one the person can change: neither hidden nor read-only. */
function personChanges(child: FieldDescriptor): boolean {
  return !child.hidden && !child.readOnly;
}

/**
 * The rules one field fails, each at its dotted path, given how a form surface read it (its
 * `value`, and whether it is `present` at all) and what the surface asks of it (whether it counts
 * as `required`, and whether its constraints are checked). A structured field, one with a presence
 * flag, a template or a bound, answers to its rules at every depth (`structuredFieldErrors`), where
 * `required` means presence: the surface's `required` at its own path, below it each member's own
 * flag, a hidden or read-only member or list item not asked. Any other field fails `required` when
 * it is required and empty (`isEmptyFieldValue`), a `null` included even where the field allows
 * one. The surface keeps its own facts: which fields it asks, how it reads them, which count as
 * required, and whether constraints are checked; the record form's `formRuleErrors` and the
 * dialogs' `dialogRuleErrors` call this for each field they ask.
 */
export function fieldRuleErrors(
  field: FieldDescriptor,
  { value, present }: { value: unknown; present: boolean },
  { required, validateConstraints }: { required: boolean; validateConstraints: boolean },
): readonly FieldRuleError[] {
  if (isStructuredPresenceField(field)) {
    return structuredFieldErrors(field, value, present, field.name, validateConstraints, required);
  }
  return required && isEmptyFieldValue(value) ? [{ path: field.name, ...REQUIRED_RULE }] : [];
}

/** Whether a descriptor opts into JSON presence/constraint validation. */
function isStructuredPresenceField(field: FieldDescriptor): boolean {
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

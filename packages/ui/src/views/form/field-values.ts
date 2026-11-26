import { fieldWidgetId, isRelationListField, type FieldDescriptor } from "../page";
import type { JsonSchemaRule } from "./validation-errors";

/** Normalize scalar text without interpreting domain-specific objects.
 * @kind null
 * @reason Format utility: converts data to display or storage text.
 */
export function textValue(value: unknown): string {
  return typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
}

/** The widgets that edit a number; their control holds `null` when it holds nothing. */
export const NUMBER_WIDGETS: ReadonlySet<string> = new Set(["integer", "float"]);
/** The widgets that pick a date, a time, or a date and time; their control holds `null` when it holds nothing. */
export const DATE_TIME_WIDGETS: ReadonlySet<string> = new Set(["date", "datetime", "time"]);
/** The widgets that hold a list; a relation list (`isRelationListField`) holds one too. */
const LIST_WIDGETS: ReadonlySet<string> = new Set(["tagInput", "list", "rows"]);

/**
 * The value `field` holds when it holds nothing: the one rule the record form's draft and seeds,
 * the action, mutation and schema dialogs and the presence control read. A lines editor's new cell
 * has a rule of its own (`emptyCellValue`, `editable-lines.ts`), which this owner does not decide.
 * First match wins; the widget is the one the descriptor resolves (`fieldWidgetId`: its `widget`,
 * else its `kind`).
 * 1. A declared `format` (a date, a date and time, or a time carried as text): `null`, whatever its
 *    kind and widget, since `""` is none of them. Then an explicit `string` kind: `""`, whatever its
 *    widget, since a controlled text input needs a text and a widget only presents. The format, never
 *    the widget, tells a date carried as text from text.
 * 2. A number widget (`NUMBER_WIDGETS`: `integer`, `float`) or a date or time widget
 *    (`DATE_TIME_WIDGETS`: `date`, `datetime`, `time`): `null`.
 * 3. The `tagInput`, `list` and `rows` widgets and a relation list (`many2many`): `[]`.
 * 4. The `switch` widget: `false`; the `object` widget: `{}`; the `json` widget: `[]` for an `array`
 *    kind, `null` for an `any` kind (a form spec's untyped value), `{}` otherwise (a widget alone).
 * 5. An `array` kind: `[]`; `object`: `{}`; `boolean` or `switch`: `false`; `integer`, `number` or
 *    `any`: `""` under the `select` widget, else `null`.
 * 6. Otherwise `""`.
 */
export function emptyValueForField(field: Pick<FieldDescriptor, "name" | "widget" | "kind" | "format">): unknown {
  if (field.format !== undefined) return null;
  if (field.kind === "string") return "";
  const widget = fieldWidgetId(field);
  if (NUMBER_WIDGETS.has(widget) || DATE_TIME_WIDGETS.has(widget)) return null;
  if (LIST_WIDGETS.has(widget) || isRelationListField(field)) return [];
  if (widget === "switch") return false;
  if (widget === "object") return {};
  if (widget === "json") return field.kind === "array" ? [] : field.kind === "any" ? null : {};
  if (field.kind === "array") return [];
  if (field.kind === "object") return {};
  if (field.kind === "boolean" || field.kind === "switch") return false;
  if (field.kind === "integer" || field.kind === "number" || field.kind === "any") return widget === "select" ? "" : null;
  return "";
}

/** What each typed FormSpec kind admits besides `null`; the untyped `any` admits every value. */
const FORM_SPEC_TYPE_ADMITS: Readonly<Record<string, (value: unknown) => boolean>> = {
  string: (value) => typeof value === "string",
  integer: (value) => typeof value === "number" && Number.isInteger(value),
  number: (value) => typeof value === "number" && Number.isFinite(value),
  boolean: (value) => typeof value === "boolean",
  object: (value) => typeof value === "object" && !Array.isArray(value),
  array: (value) => Array.isArray(value),
};

/**
 * Whether `value` is one the declared FormSpec type of `field` admits: `null` only where the field
 * is `nullable`, whatever its kind; otherwise a value of its `kind`, any value for `any`. A field
 * without a FormSpec kind admits no other value.
 */
export function isFormSpecValueCompatible(field: Pick<FieldDescriptor, "kind" | "nullable">, value: unknown): boolean {
  if (value === null) return Boolean(field.nullable);
  if (field.kind === "any") return true;
  return field.kind !== undefined && Object.hasOwn(FORM_SPEC_TYPE_ADMITS, field.kind)
    && FORM_SPEC_TYPE_ADMITS[field.kind]!(value);
}

/**
 * Whether a member that may be left out stays absent rather than take `seed`: it is not `required`
 * and its own declared type, a typed FormSpec kind, refuses the seed (`isFormSpecValueCompatible`),
 * as `null` is refused by a number that is not `nullable`. A required member, an untyped one (`any`)
 * and one without a FormSpec kind take their seed. The one refusal check the three seeding paths
 * read: a schema dialog's (`formSpecInitialValues`), `MutationDialog`'s and the record form's
 * create draft (`emptyDraft`). The record form's edit baseline (`recordToValues`) does not read it:
 * a member the record lacks takes its empty value there.
 */
export function refusesSeed(field: Pick<FieldDescriptor, "kind" | "nullable" | "required">, seed: unknown): boolean {
  return !field.required && field.kind !== undefined && Object.hasOwn(FORM_SPEC_TYPE_ADMITS, field.kind)
    && !isFormSpecValueCompatible(field, seed);
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
 * presence-required, or holds the key. One `null` is not held against the key, whoever put it
 * there: a `null` equal to the field's own empty value (its `emptyValueForField`, which
 * `refusesSeed` does not refuse), the one a form seeds a field with when it holds nothing, so a
 * form never refuses its own seed in an optional field. Any other value is a value,
 * a `null` the field allows, a blank text and an empty list included: whether it is acceptable is
 * the descriptor's constraints' to say, unless `validateConstraints` is off and a schema resolver
 * reports them.
 */
function failedValueRule(
  field: FieldDescriptor,
  value: unknown,
  present: boolean,
  required: boolean,
  validateConstraints: boolean,
): JsonSchemaRule | undefined {
  if (!present || value === undefined || (value === null && !field.nullable)) {
    const seeded = value === null && emptyValueForField(field) === null && !refusesSeed(field, null);
    return required || field.presenceRequired || (present && !seeded) ? REQUIRED_RULE : undefined;
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
 * not allow) fails `required` where the field is required, presence-required, or holds the key
 * with anything but the `null` a form seeds it with when it holds nothing (`failedValueRule`),
 * while a `null` it allows, a blank text, an empty list and any object are values, which the
 * descriptor's own constraints (`minLength`, `minItems`, …) judge. The field's own path is
 * required when `required` says so (by default its own flag); each member answers to its own
 * flag, and a list item is always required. One case reads as presence: an empty text under a
 * `minLength` of one is `required` when the field is neither omittable nor nullable. With
 * `validateConstraints=false`, only presence is checked, at every depth. Below the field's own
 * path, a member or a list item that is `hidden` or
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
    // A list item is always required: it has no key to leave out, so an item that holds nothing is missing.
    value.forEach((item, index) => {
      errors.push(...structuredFieldErrors(field.itemTemplate!, item, true, `${path}.${index}`, validateConstraints, true));
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
 * flag and each list item always required, a hidden or read-only member or list item not asked.
 * Any other field fails `required` when
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

import * as React from "react";
import type { ErrorObject } from "ajv";
import {
  extractActionOutcome, publicGraphQLErrorsFromUnknown, type ActionOutcome, type AuthoredMutateOptions,
} from "@angee/refine";
import { get, set, type FieldErrors, type FieldValues, type Path, type UseFormReturn } from "react-hook-form";
import { errorFromUnknown, graphQLErrorsFromUnknown } from "../../data/errors";
import type { UiTranslate } from "../../i18n";
import type { FieldDescriptor } from "../page";
import type { FieldRuleError } from "./field-values";

/**
 * The call options of a surface that renders a submission's failure itself (a dialog's
 * banner, an action form's fields, an action's settle toast): refine's generic error
 * notification is off, so the failure is reported once. Pass them on to the authored
 * call the submission makes.
 * @kind null
 * @reason Transport infrastructure: the one quiet-call value form surfaces hand their callers.
 */
export const SURFACE_RENDERS_FAILURE: AuthoredMutateOptions = { errorNotification: false };

export type DottedPathFieldErrorMap = Readonly<
  Record<string, readonly string[]>
>;

export interface DottedPathFieldErrors {
  replace: (errors: DottedPathFieldErrorMap) => void;
  clear: () => void;
  messagesFor: (field: string) => readonly string[];
  clearField: (field: string) => void;
  formSummary: string | null;
}

/** Field- and form-level validation messages extracted from a save failure. */
export interface ValidationErrors {
  /** Messages keyed by the wire field name in the schema's naming convention (snake_case on Hasura resources). */
  fieldErrors: Record<string, readonly string[]>;
  /** Non-field / form-level messages. */
  formErrors: readonly string[];
}

/**
 * A failed rule in JSON Schema's vocabulary: its keyword and the keyword's parameters, as an Ajv
 * error carries them. Read-only, parameters included, since one rule object may be shared
 * (`REQUIRED_RULE` is frozen).
 */
export type JsonSchemaRule = Readonly<Pick<ErrorObject<string, Readonly<Record<string, unknown>>>, "keyword" | "params">>;

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

/**
 * The shared UI copy for a failed rule, whether Ajv reported it or a form checked a descriptor's
 * own constraints (`structuredFieldErrors`); the caller supplies its active UI translator. It
 * words the keyword it is given and decides nothing else: whether an empty text counts as missing
 * is decided where the rule is found. A limit that counts characters or items is the `count` its
 * message's plural forms are chosen by.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function jsonSchemaErrorMessage({ keyword, params }: JsonSchemaRule, t: UiTranslate): string {
  switch (keyword) {
    case "required": return t("form.required");
    case "enum": case "discriminator": return t("form.validation.choice");
    case "type": {
      const type = typeLabel(params.type, t);
      return type ? t("form.validation.type", { type }) : t("form.invalidValue");
    }
    case "minLength": return t("form.validation.minLength", { count: Number(params.limit) });
    case "maxLength": return t("form.validation.maxLength", { count: Number(params.limit) });
    case "minimum": return t("form.validation.minimum", { limit: Number(params.limit) });
    case "maximum": return t("form.validation.maximum", { limit: Number(params.limit) });
    case "exclusiveMinimum": return t("form.validation.exclusiveMinimum", { limit: Number(params.limit) });
    case "exclusiveMaximum": return t("form.validation.exclusiveMaximum", { limit: Number(params.limit) });
    case "minItems": return t("form.validation.minItems", { count: Number(params.limit) });
    case "maxItems": return t("form.validation.maxItems", { count: Number(params.limit) });
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

/**
 * A form's errors for the rules its fields fail, set at each rule's dotted path: an error's
 * `type` is the failed rule's keyword and its message that rule's shared copy
 * (`jsonSchemaErrorMessage`).
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function fieldErrorsFromRules(
  failed: readonly FieldRuleError[],
  t: UiTranslate,
): FieldErrors<Record<string, unknown>> {
  const errors: FieldErrors<Record<string, unknown>> = {};
  for (const error of failed) set(errors, error.path, { type: error.keyword, message: jsonSchemaErrorMessage(error, t) });
  return errors;
}

/**
 * The `type` of an object field's error that is its members' errors. An object field, one that
 * owns an `objectTemplate`, is one registered control whose members report their errors under
 * its own node; `markMemberErrors` gives that node this `type` and no message of its own. Its
 * messages are its members'.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export const MEMBER_ERRORS = "memberErrors";

/**
 * A resolver's error tree in the shape react-hook-form keeps. At a change the library keeps the
 * changed field's error only when it has a `type` or a `message`, or is an array, so the node of
 * an object field whose members failed, a plain object of their errors, would be dropped, and
 * their messages with it, while the form stays invalid. Of the `fields` a form shows, each object
 * field (one that owns an `objectTemplate`) whose node is a plain object with neither a `type` nor
 * a `message` of its own gets `type: MEMBER_ERRORS` and no message: the field error that also
 * carries its members' errors, as the library's type for an object-valued field describes it. A
 * field without a template (a `json` field holding an object, a root of dotted fields, a scalar)
 * is never marked, whatever sits under its path; nor is an object field's leaf error, the root
 * above it, an object member inside it, or an object field with no error. Returns `errors`,
 * marked in place.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function markMemberErrors<TErrors extends object>(
  errors: TErrors,
  fields: Iterable<Pick<FieldDescriptor, "name" | "objectTemplate">>,
): TErrors {
  for (const field of fields) {
    if (!field.objectTemplate) continue;
    const node: unknown = get(errors, field.name);
    if (node && typeof node === "object" && !Array.isArray(node) && !("type" in node) && !("message" in node)) {
      Object.assign(node, { type: MEMBER_ERRORS });
    }
  }
  return errors;
}

/** A save either acknowledges data, reports validation issues, or rejects a stale edit. */
export type FormSubmitResult<TData> =
  | { status: "ok"; data: TData; message?: string }
  | { status: "invalid"; issues: ValidationErrors }
  | { status: "conflict"; message: string; field?: string };

/** Decode a wire action response through the transport owner.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function actionFormSubmitResult(data: unknown, root: string): FormSubmitResult<ActionOutcome> {
  return actionOutcomeSubmitResult(extractActionOutcome(data, root));
}

/** Adapt an already normalized action outcome without decoding it again.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function actionOutcomeSubmitResult(outcome: ActionOutcome | null | undefined): FormSubmitResult<ActionOutcome> {
  return outcome?.ok
    ? { status: "ok", data: outcome, message: outcome.message }
    : invalidFormSubmit({
        fieldErrors: outcome?.validationErrors ?? {},
        formErrors: outcome?.message ? [outcome.message] : [],
      });
}

/**
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function invalidFormSubmit(issues: ValidationErrors): Extract<FormSubmitResult<never>, { status: "invalid" }> {
  return { status: "invalid", issues };
}

/** Missing mutation data cannot acknowledge a saved form.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function savedFormSubmitResult<TData>(data: TData | null | undefined, missingMessage: string): FormSubmitResult<TData> {
  return data == null ? formSubmitError(missingMessage) : { status: "ok", data };
}

/** Decode a transport failure; contract violations remain developer errors.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function formSubmitError(cause: unknown, fallback?: string): Exclude<FormSubmitResult<never>, { status: "ok" }> {
  if (cause instanceof FormSubmitContractError) throw cause;
  if (publicGraphQLErrorsFromUnknown(cause).some((item) =>
    item.extensions.code === "STALE_REVISION" || item.extensions.code === "CREATION_KEY_CONFLICT")) {
    return { status: "conflict", message: validationErrorMessage(cause, fallback ?? "Could not save record.") };
  }
  return invalidFormSubmit(validationErrorsFromError(cause, fallback));
}

class FormSubmitContractError extends TypeError {
  constructor() {
    super('FormSubmitResult contract requires { status: "ok", data }, { status: "invalid", issues }, or { status: "conflict", message }.');
    this.name = "FormSubmitContractError";
  }
}

function assertNeverResult(_result: never): never {
  throw new FormSubmitContractError();
}

/** Narrow one submit result and bind its issues to RHF; malformed results fail loudly.
 * A failure is shown once: a message bound to a rendered field stays under that field,
 * and `fallback` stands in only for a failure no field and no form-level message names.
 * The form-level messages stay several: `root.server` holds them as a list (its `types`),
 * read with `formErrorsFromForm`; its `message` is their text, one per line.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function applyFormErrors<TValues extends FieldValues, TData>(
  form: Pick<UseFormReturn<TValues>, "setError">,
  result: FormSubmitResult<TData>,
  options: {
    fieldNames?: Iterable<string>;
    fieldSummary?: (errors: DottedPathFieldErrorMap) => string;
    fallback?: string;
    /**
     * Bring the person to the refusal: move focus to the first rendered field it names, in the
     * order of `fieldNames` (the server's order without them), through the form library's own
     * `shouldFocus`. A submit's answer asks for it; an owner that projects issues while the person
     * edits elsewhere does not.
     */
    focus?: boolean;
  } = {},
): result is Exclude<FormSubmitResult<TData>, { status: "ok" }> {
  let issues: ValidationErrors;
  let type: "server" | "conflict";
  if (result == null || typeof result !== "object") throw new FormSubmitContractError();
  switch (result.status) {
    case "ok":
      if (!Object.hasOwn(result, "data")) throw new FormSubmitContractError();
      return false;
    case "invalid":
      if (!result.issues || !isStringListMap(result.issues.fieldErrors)
        || !Array.isArray(result.issues.formErrors) || !result.issues.formErrors.every((message) => typeof message === "string")) {
        throw new FormSubmitContractError();
      }
      issues = result.issues;
      type = "server";
      break;
    case "conflict":
      if (typeof result.message !== "string" || (result.field !== undefined && typeof result.field !== "string")) throw new FormSubmitContractError();
      issues = { fieldErrors: result.field ? { [result.field]: [result.message] } : {}, formErrors: [result.message] };
      type = "conflict";
      break;
    default:
      return assertNeverResult(result);
  }
  const fieldNames = options.fieldNames === undefined ? undefined : [...options.fieldNames];
  const named = Object.keys(issues.fieldErrors).filter((name) => issues.fieldErrors[name]!.length > 0);
  const focused = !options.focus ? undefined
    : fieldNames ? fieldNames.flatMap((field) => named.filter((name) => dottedPathBelongsToField(name, field)))[0] : named[0];
  const bound: Record<string, readonly string[]> = {};
  const unclaimed: Record<string, readonly string[]> = {};
  for (const [name, messages] of Object.entries(issues.fieldErrors)) {
    if (messages.length === 0) continue;
    form.setError(name as Path<TValues>, {
      type, message: messages.join(" "), types: { [type]: [...messages] },
    }, name === focused ? { shouldFocus: true } : undefined);
    const target = fieldNames && !fieldNames.some((field) => dottedPathBelongsToField(name, field)) ? unclaimed : bound;
    target[name] = messages;
  }
  const messages = [...issues.formErrors];
  if (messages.length === 0 && Object.keys(bound).length && options.fieldSummary) {
    messages.push(options.fieldSummary(bound));
  }
  messages.push(...dottedPathErrorMessages(unclaimed));
  if (messages.length === 0 && Object.keys(bound).length === 0 && options.fallback) messages.push(options.fallback);
  if (messages.length > 0) {
    form.setError("root.server" as Path<TValues>, { type, message: messages.join("\n"), types: { [type]: messages } });
  }
  return true;
}

/**
 * The form-level messages a submit left on the form (`root.server`), each its own entry: the list
 * `applyFormErrors` stored, or the one message another owner set there. Empty when there is none.
 */
export function formErrorsFromForm(errors: object): readonly string[] {
  const server = "root" in errors && errors.root && typeof errors.root === "object" && "server" in errors.root ? errors.root.server : undefined;
  if (!server || typeof server !== "object") return EMPTY_FIELD_NAMES;
  const type = "type" in server && typeof server.type === "string" ? server.type : undefined;
  const listed = type && "types" in server && server.types && typeof server.types === "object" ? (server.types as Record<string, unknown>)[type] : undefined;
  // The stored list itself, so its identity changes exactly when a new answer lands.
  if (Array.isArray(listed) && listed.every((message) => typeof message === "string")) return listed;
  return "message" in server && typeof server.message === "string" && server.message ? [server.message] : EMPTY_FIELD_NAMES;
}

/**
 * Project `<field>.<row index>.<child field>` errors into the row-indexed
 * validation contract consumed by editable line cells.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function lineRowErrorsFromDottedPaths(
  errors: DottedPathFieldErrorMap,
  field: string,
): readonly (ValidationErrors | undefined)[] | undefined {
  const prefix = `${field}.`;
  const byRow = new Map<number, ValidationErrors>();
  for (const [path, messages] of Object.entries(errors)) {
    if (!path.startsWith(prefix)) continue;
    const match = /^(\d+)\.(.+)$/.exec(path.slice(prefix.length));
    if (!match) continue;
    const index = Number(match[1]);
    const childField = match[2] as string;
    const entry = byRow.get(index) ?? { fieldErrors: {}, formErrors: [] };
    entry.fieldErrors[childField] = [
      ...(entry.fieldErrors[childField] ?? []),
      ...messages,
    ];
    byRow.set(index, entry);
  }
  if (byRow.size === 0) return undefined;
  const rows: (ValidationErrors | undefined)[] = [];
  const maxIndex = Math.max(...byRow.keys());
  for (let index = 0; index <= maxIndex; index += 1) rows.push(byRow.get(index));
  return rows;
}

/**
 * Extract per-field and form-level validation messages from a mutation error.
 * The GraphQL runtime surfaces Django model-validation failures as structured
 * extensions; the base form binds field messages and shows the rest at form
 * level.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function validationErrorsFromError(error: unknown, fallback = "Could not save record."): ValidationErrors {
  const fieldErrors: Record<string, string[]> = {};
  const formErrors: string[] = [];
  let structured = false;

  for (const graphQLError of graphQLErrorsFromUnknown(error)) {
    const extensionsValue = graphQLError.extensions;
    const extensions = extensionsValue && typeof extensionsValue === "object"
      ? extensionsValue as Record<string, unknown>
      : undefined;
    const validation = validationErrorMap(extensions?.validationErrors);
    if (validation) {
      structured = true;
      for (const [field, messages] of Object.entries(validation)) {
        fieldErrors[field] = [...(fieldErrors[field] ?? []), ...messages];
      }
    }
    const form = extensions?.formErrors;
    if (Array.isArray(form)) {
      structured = true;
      for (const message of form) {
        if (typeof message === "string") formErrors.push(message);
      }
    }
  }

  if (!structured) {
    const message = validationErrorMessage(error, fallback);
    if (message) formErrors.push(message);
  }
  return { fieldErrors, formErrors };
}

/** Parse an opaque JSON scalar as a field-to-messages validation map.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function validationErrorMap(
  value: unknown,
): Record<string, string[]> | null {
  if (!isStringListMap(value)) return null;
  return Object.fromEntries(
    Object.entries(value).map(([field, messages]) => [field, [...messages]]),
  );
}

/** Format an opaque field-to-messages validation map for a summary surface.
 * @kind null
 * @reason Codec and transport infrastructure: translates runtime data and addresses.
 */
export function validationErrorMessages(value: unknown): string[] {
  const errors = validationErrorMap(value);
  if (!errors) return [];
  return Object.entries(errors).flatMap(([field, messages]) =>
    messages.map((message) => `${field}: ${message}`),
  );
}

/**
 * Re-scope the descendant-message strings returned by
 * {@link useDottedPathFieldErrors} or `fieldErrorMessages` to one nested dotted
 * path. Exact messages lose their path prefix; deeper descendants retain it
 * for another nested owner. Matching keeps the owner's exact-or-dot-boundary rule.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function messagesForDottedPath(
  messages: readonly string[],
  path: string,
): readonly string[] {
  const exactPrefix = `${path}: `;
  const descendantPrefix = `${path}.`;
  return messages.flatMap((message) => {
    if (message.startsWith(exactPrefix)) {
      return [message.slice(exactPrefix.length)];
    }
    return message.startsWith(descendantPrefix) ? [message] : [];
  });
}

/**
 * Direct messages from a scoped list returned by `fieldErrorMessages` or
 * {@link useDottedPathFieldErrors}: exact-path messages are bare, while dotted
 * descendants retain their prefixes and are excluded here.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function directDottedPathMessages(
  messages: readonly string[],
  path: string,
): readonly string[] {
  const descendantPrefix = `${path}.`;
  return messages.filter((message) => !message.startsWith(descendantPrefix));
}

/**
 * Own a field-to-messages map whose keys may address nested values with dotted
 * paths. A field binds its exact key and descendants, editing it clears the
 * same boundary, and keys belonging to no rendered field fold into one form
 * summary.
 * @kind null
 * @reason Runtime infrastructure: composition, state, validation or lifecycle support without rendered anatomy.
 */
export function useDottedPathFieldErrors(
  fieldNames: readonly string[] = EMPTY_FIELD_NAMES,
): DottedPathFieldErrors {
  const [errors, setErrors] = React.useState<DottedPathFieldErrorMap>({});
  const replace = React.useCallback(
    (next: DottedPathFieldErrorMap) => setErrors(next),
    [],
  );
  const clear = React.useCallback(() => setErrors({}), []);
  const messagesFor = React.useCallback(
    (field: string): readonly string[] =>
      Object.entries(errors).flatMap(([path, messages]) => {
        if (path === field) return messages;
        if (!dottedPathBelongsToField(path, field)) return [];
        return messages.map((message) => `${path}: ${message}`);
      }),
    [errors],
  );
  const clearField = React.useCallback((field: string) => {
    setErrors((current) =>
      Object.fromEntries(
        Object.entries(current).filter(
          ([path]) => !dottedPathBelongsToField(path, field),
        ),
      ),
    );
  }, []);
  const formSummary = React.useMemo(
    () =>
      dottedPathErrorSummary(
        Object.fromEntries(
          Object.entries(errors).filter(
            ([path]) =>
              !fieldNames.some((field) =>
                dottedPathBelongsToField(path, field),
              ),
          ),
        ),
      ),
    [errors, fieldNames],
  );
  return { replace, clear, messagesFor, clearField, formSummary };
}

function dottedPathBelongsToField(path: string, field: string): boolean {
  return path === field || path.startsWith(`${field}.`);
}

/** One `field: message` entry per message of a path no rendered field claims. */
function dottedPathErrorMessages(errors: DottedPathFieldErrorMap): string[] {
  return Object.entries(errors).flatMap(([field, entries]) => entries.map((message) => `${field}: ${message}`));
}

function dottedPathErrorSummary(
  errors: DottedPathFieldErrorMap,
): string | null {
  const messages = dottedPathErrorMessages(errors);
  return messages.length > 0 ? messages.join(" ") : null;
}

function isStringListMap(value: unknown): value is Record<string, string[]> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return Object.values(value).every(
    (entry) =>
      Array.isArray(entry) && entry.every((item) => typeof item === "string"),
  );
}

function validationErrorMessage(error: unknown, fallback: string): string {
  const safe = errorFromUnknown(error);
  if (safe) return safe.message.replace(/^\[\w+\]\s*/, "");
  if (typeof error === "string") return error.replace(/^\[\w+\]\s*/, "");
  return fallback;
}

const EMPTY_FIELD_NAMES: readonly string[] = [];

/** Project RHF server errors into the backend dotted-path display contract. */
export function serverErrorsFromForm(errors: object): Record<string, readonly string[]> {
  const result: Record<string, readonly string[]> = {};
  const visit = (value: unknown, path: string): void => {
    if (!value || typeof value !== "object") return;
    if ("type" in value && value.type === "server" && "message" in value && typeof value.message === "string") {
      if (!path.startsWith("root.")) {
        const messages = "types" in value && value.types && typeof value.types === "object" && "server" in value.types ? value.types.server : undefined;
        result[path] = Array.isArray(messages) ? messages.filter((message): message is string => typeof message === "string") : [value.message];
      }
      return;
    }
    for (const [name, child] of Object.entries(value)) {
      if (name !== "ref" && name !== "types") visit(child, path ? `${path}.${name}` : name);
    }
  };
  visit(errors, "");
  return result;
}

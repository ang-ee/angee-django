import * as React from "react";

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
  /** Messages keyed by SDL (camelCase) field name. */
  fieldErrors: Record<string, string[]>;
  /** Non-field / form-level messages. */
  formErrors: string[];
}

interface GraphQLErrorLike {
  message?: unknown;
  extensions?: Record<string, unknown> | null;
}

/**
 * Extract per-field and form-level validation messages from a mutation error.
 * The GraphQL runtime surfaces Django model-validation failures as structured
 * extensions; the base form binds field messages and shows the rest at form
 * level.
 */
export function validationErrorsFromError(error: unknown): ValidationErrors {
  const fieldErrors: Record<string, string[]> = {};
  const formErrors: string[] = [];
  let structured = false;

  for (const graphQLError of graphQLErrorsOf(error)) {
    const extensions = graphQLError.extensions ?? undefined;
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
    const message = validationErrorMessage(error);
    if (message) formErrors.push(message);
  }
  return { fieldErrors, formErrors };
}

/** Parse an opaque JSON scalar as a field-to-messages validation map. */
export function validationErrorMap(
  value: unknown,
): Record<string, string[]> | null {
  if (!isStringListMap(value)) return null;
  return Object.fromEntries(
    Object.entries(value).map(([field, messages]) => [field, [...messages]]),
  );
}

/**
 * Own a field-to-messages map whose keys may address nested values with dotted
 * paths. A field binds its exact key and descendants, editing it clears the
 * same boundary, and keys belonging to no rendered field fold into one form
 * summary.
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

function dottedPathErrorSummary(
  errors: DottedPathFieldErrorMap,
): string | null {
  const messages = Object.entries(errors).flatMap(([field, entries]) =>
    entries.map((message) => `${field}: ${message}`),
  );
  return messages.length > 0 ? messages.join(" ") : null;
}

function graphQLErrorsOf(error: unknown): readonly GraphQLErrorLike[] {
  if (error && typeof error === "object" && "graphQLErrors" in error) {
    const list = (error as { graphQLErrors?: unknown }).graphQLErrors;
    if (Array.isArray(list)) return list as GraphQLErrorLike[];
  }
  return [];
}

function isStringListMap(value: unknown): value is Record<string, string[]> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return Object.values(value).every(
    (entry) =>
      Array.isArray(entry) && entry.every((item) => typeof item === "string"),
  );
}

function validationErrorMessage(error: unknown): string {
  if (error instanceof Error) return error.message.replace(/^\[\w+\]\s*/, "");
  if (typeof error === "string") return error.replace(/^\[\w+\]\s*/, "");
  return "Could not save record.";
}

const EMPTY_FIELD_NAMES: readonly string[] = [];

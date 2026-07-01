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
    const validation = extensions?.validationErrors;
    if (isStringListMap(validation)) {
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

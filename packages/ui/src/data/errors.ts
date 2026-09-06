export function errorFromUnknown(error: unknown): Error | null {
  if (!error) return null;
  const record = objectValue(error);
  if (record && ("request" in record || "response" in record || "graphQLErrors" in record)) {
    const messages = graphQLErrorsFromUnknown(error)
      .flatMap((item) => {
        const itemRecord = objectValue(item);
        const message = itemRecord?.message;
        const code = objectValue(itemRecord?.extensions)?.code;
        return typeof message === "string" && message.trim() && isPublicGraphQLErrorCode(code)
          ? [message]
          : [];
      });
    return new Error(messages.length > 0 ? messages.join(" ") : "Request failed.");
  }
  if (error instanceof Error) return error;
  if (typeof error === "string" || typeof error === "number" || typeof error === "boolean") {
    return new Error(String(error));
  }
  return null;
}

/** Read the two native GraphQL client error containers through one owner. */
export function graphQLErrorsFromUnknown(error: unknown): readonly Record<string, unknown>[] {
  const record = objectValue(error);
  if (!record) return [];
  const response = objectValue(record.response);
  return [record.graphQLErrors, response?.errors]
    .flatMap((value) => Array.isArray(value) ? value : [])
    .flatMap((item) => {
      const itemRecord = objectValue(item);
      return itemRecord ? [itemRecord] : [];
    });
}

function isPublicGraphQLErrorCode(code: unknown): boolean {
  return code === "VALIDATION" || code === "BAD_USER_INPUT" ||
    code === "UNAUTHENTICATED" || code === "PERMISSION_DENIED" || code === "FORBIDDEN";
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value != null && typeof value === "object"
    ? value as Record<string, unknown>
    : null;
}

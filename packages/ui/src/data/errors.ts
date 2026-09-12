import {
  publicGraphQLErrorsFromUnknown,
  type PublicGraphQLError,
} from "@angee/refine";

export function errorFromUnknown(error: unknown): Error | null {
  if (!error) return null;
  const record = objectValue(error);
  if (record && ("request" in record || "response" in record || "graphQLErrors" in record)) {
    const messages = graphQLErrorsFromUnknown(error).map((item) => item.message);
    return new Error(messages.length > 0 ? messages.join(" ") : "Request failed.");
  }
  if (error instanceof Error) return error;
  if (typeof record?.message === "string" && record.message.trim() !== "") {
    return new Error(record.message);
  }
  if (typeof error === "string" || typeof error === "number" || typeof error === "boolean") {
    return new Error(String(error));
  }
  return null;
}

/** Read the two native GraphQL client error containers through one owner. */
export function graphQLErrorsFromUnknown(error: unknown): readonly PublicGraphQLError[] {
  return publicGraphQLErrorsFromUnknown(error);
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value != null && typeof value === "object"
    ? value as Record<string, unknown>
    : null;
}

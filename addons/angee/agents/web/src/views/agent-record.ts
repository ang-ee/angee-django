import type { Row } from "@angee/sdk";

/** Read a string field off the boundary record (`Record<string, unknown>`), or "". */
export function stringField(record: Row | null, key: string): string {
  const value = record?.[key];
  return typeof value === "string" ? value : "";
}

// The agent carries two state axes, each reading as its UPPERCASE enum name:
// `lifecycle` is the provision journey and `runtimeStatus` is observed health.
export function agentLifecycle(record: Row | null): string {
  return stringField(record, "lifecycle").toUpperCase();
}

export function agentRuntime(record: Row | null): string {
  return stringField(record, "runtimeStatus").toUpperCase();
}

import type {
  Row,
} from "@angee/metadata";

/** Read a string field off the boundary record (`Record<string, unknown>`), or "". */
export function stringField(record: Row | null, key: string): string {
  const value = record?.[key];
  return typeof value === "string" ? value : "";
}

/** Read a backend-owned boolean fact off the boundary record. */
export function booleanField(record: Row | null, key: string): boolean {
  return record?.[key] === true;
}

// The agent carries two state axes, each reading as its UPPERCASE enum name:
// `lifecycle` is the provision journey and `runtime_status` is observed health.
export function agentLifecycle(record: Row | null): string {
  return stringField(record, "lifecycle").toUpperCase();
}

export function agentRuntime(record: Row | null): string {
  return stringField(record, "runtime_status").toUpperCase();
}

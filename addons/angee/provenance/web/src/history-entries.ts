// Pure projections of history entries for rendering; no React, no transport.

/** How a stored history value renders: nothing, a line of text, or a JSON block. */
export type HistoryValue =
  | { kind: "empty" }
  | { kind: "text"; text: string }
  | { kind: "json"; text: string };

/** Scalars render as text; structured values keep their JSON shape for a code block. */
export function historyValue(value: unknown): HistoryValue {
  if (value === null || value === undefined || value === "") return { kind: "empty" };
  if (typeof value === "string") return { kind: "text", text: value };
  if (typeof value === "number" || typeof value === "boolean") return { kind: "text", text: String(value) };
  return { kind: "json", text: JSON.stringify(value, null, 2) };
}

export type StatementVerb = "asserted" | "applied" | "withdrawn" | "quarantined" | "deprecated";

/**
 * The verb a statement version states: its first version asserts, a later
 * version applies when survivorship picked it, otherwise its status speaks.
 */
export function statementVerb(statement: {
  history_type: string;
  status: string;
  applied: boolean;
}): StatementVerb {
  const status = statement.status.toLowerCase();
  if (status === "withdrawn" || status === "quarantined" || status === "deprecated") return status;
  if (statement.history_type === "+") return "asserted";
  return statement.applied ? "applied" : "asserted";
}

/** The step tone of an entry: failures and reverts stand out, merges inform, edits pass. */
export function entryTone(entry: { activity: string; outcome?: string | null }): "danger" | "warning" | "info" | "success" {
  if ((entry.outcome ?? "").toLowerCase() === "failure") return "danger";
  const activity = entry.activity.toLowerCase();
  if (activity === "revert") return "warning";
  if (activity === "merge") return "info";
  return "success";
}

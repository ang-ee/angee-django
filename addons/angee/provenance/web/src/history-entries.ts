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

type Translate = (key: string, options?: Record<string, unknown>) => string;

/** The changeset action's label; every key is named here so the bundle liveness rule sees it. */
export function actionLabel(t: Translate, action: string | null | undefined): string {
  switch (optionToken(action)) {
    case "create":
      return t("history.action.create");
    case "update":
      return t("history.action.update");
    case "delete":
      return t("history.action.delete");
    default:
      return optionToken(action);
  }
}

/** The changeset activity's label, keyed statically for the same reason. */
export function activityLabel(t: Translate, activity: string | null | undefined): string {
  switch (optionToken(activity)) {
    case "manual":
      return t("history.activity.manual");
    case "pull":
      return t("history.activity.pull");
    case "push":
      return t("history.activity.push");
    case "import":
      return t("history.activity.import");
    case "merge":
      return t("history.activity.merge");
    case "revert":
      return t("history.activity.revert");
    case "seed":
      return t("history.activity.seed");
    case "system":
      return t("history.activity.system");
    default:
      return optionToken(activity);
  }
}

/** A statement version's verb label (asserted, applied, withdrawn, quarantined, deprecated). */
export function statementVerbLabel(t: Translate, verb: string): string {
  switch (verb) {
    case "asserted":
      return t("history.statement.asserted");
    case "applied":
      return t("history.statement.applied");
    case "withdrawn":
      return t("history.statement.withdrawn");
    case "quarantined":
      return t("history.statement.quarantined");
    case "deprecated":
      return t("history.statement.deprecated");
    default:
      return verb;
  }
}

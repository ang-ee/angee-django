import { createNamespaceT } from "@angee/ui/i18n";

export const enChecklistsMessages: Record<string, string> = {
  "status.passed": "Passed",
  "status.risk": "Risk",
  "status.conflict": "Conflict",
  "status.unresolved": "Unresolved",
  "status.not_checked": "Not checked",
  "status.not_applicable": "Not applicable",
  "status.unknown": "Unknown",
  "checks": "Checks",
  "status": "Status",
  "result": "Result",
  "evidence": "Evidence",
  "recordedBy": "Recorded evidence",
  "title": "Checks",
  "help": "Recorded check results. An accepted risk or conflict stays visible.",
  "changed": "The record changed after these checks",
  "changedHelp": "Earlier passes are now unresolved. Check the changed record before relying on them.",
  "incomplete": "Incomplete",
  "stale": "Out of date",
  "noResults": "No check results are available. A record is not checked until results are recorded.",
  "noResultsShort": "No results",
  "loading": "Loading checks…",
};

export const useChecklistsT = createNamespaceT("checklists", enChecklistsMessages);

import { recordTargetHref, recordTargetSearch } from "@angee/ui";

/** The workflows addon owns its record-bound decision selection. */
export const DECISION_SEARCH_KEY = "decision";

export function decisionSearch(search: Readonly<Record<string, unknown>>, decision: string | null): Record<string, unknown> {
  return recordTargetSearch(search, { search: { [DECISION_SEARCH_KEY]: decision } });
}

export function decisionHref(href: string, decision: string, tab?: string | null): string {
  return recordTargetHref(href, { tab, search: { [DECISION_SEARCH_KEY]: decision } });
}

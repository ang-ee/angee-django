import { CHATTER_TAB_SEARCH_KEY, recordTargetHref, recordTargetSearch } from "@angee/ui";

/** The workflows addon owns its record-bound decision selection. */
export const DECISION_SEARCH_KEY = "decision";
export const WORKFLOW_RUN_SEARCH_KEY = "workflowRun";

export function decisionSearch(search: Readonly<Record<string, unknown>>, decision: string | null): Record<string, unknown> {
  return recordTargetSearch(search, { search: {
    [CHATTER_TAB_SEARCH_KEY]: decision ? "workflows" : null,
    [DECISION_SEARCH_KEY]: decision,
    [WORKFLOW_RUN_SEARCH_KEY]: null,
  } });
}

export function workflowSubjectActionSearch(
  search: Readonly<Record<string, unknown>>,
  runId: string,
): Record<string, unknown> {
  return recordTargetSearch(search, { search: {
    [CHATTER_TAB_SEARCH_KEY]: "workflows",
    [DECISION_SEARCH_KEY]: null,
    [WORKFLOW_RUN_SEARCH_KEY]: runId,
  } });
}

export function decisionHref(href: string, decision: string, tab?: string | null): string {
  return recordTargetHref(href, { tab, search: {
    [CHATTER_TAB_SEARCH_KEY]: "workflows",
    [DECISION_SEARCH_KEY]: decision,
    [WORKFLOW_RUN_SEARCH_KEY]: null,
  } });
}

/** Keep a route-selected Decision inside the canonical direct/artifact subject history. */
export function subjectDecisionRunId(
  decisionRunId: string | null | undefined,
  subjectRuns: ReadonlyArray<{ id: string }>,
): string | null {
  if (!decisionRunId) return null;
  return subjectRuns.some((run) => run.id === decisionRunId) ? decisionRunId : null;
}

/** Admit a route-selected Decision only when the subject history returned it. */
export function subjectDecision<T extends { id: string }>(
  decisionId: string | null,
  decisions: readonly T[],
): T | undefined {
  return decisionId ? decisions.find((decision) => decision.id === decisionId) : undefined;
}

export function subjectPendingDecision<T extends { step_run?: { run?: { id: string } | null } | null }>(
  decisions: readonly T[],
  followedRunId: string | null,
): T | undefined {
  return followedRunId
    ? decisions.find((item) => item.step_run?.run?.id === followedRunId)
    : decisions[0];
}

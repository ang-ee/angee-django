import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { useAuthoredQuery } from "@angee/refine";
import type { ActivityEntry, ActivityEntrySource, ActivityRecord, ActivityWindow } from "@angee/ui";
import * as React from "react";

import { enDecisionsMessages } from "./i18n";
import { DECISION_MODEL, RecordDecisionAnswersDocument } from "./documents.console";
import { DecisionCard } from "./DecisionCard";

const decisionAnswerActivitySource = {
  kinds: ["decision"],
  useEntries: useDecisionAnswerEntries,
} satisfies ActivityEntrySource;

export { DECISION_MODEL } from "./documents.console";
export { DecisionCard, type DecisionCardProps } from "./DecisionCard";
export { decisionFieldMarks } from "./proposal";
export { useDecision } from "./origin";
export { type Decision } from "./documents.console";

export default defineBaseAddon({
  id: "decisions",
  routes: resourcePageRoutes(
    "decisions.inbox", "/decisions",
    lazyRouteComponent(() => import("./InboxPage"), "InboxPage"),
    "decisions.Decision",
    { defaultResourceView: "decisions.inbox" },
  ),
  resourceViews: [
    { id: "decisions.inbox", label: "Inbox", resource: DECISION_MODEL, filter: { is_open: { exact: true } } },
    { id: "decisions.waiting", label: "Waiting on me", resource: DECISION_MODEL,
      filter: { is_open: { exact: true }, can_act: { exact: true } } },
    { id: "decisions.all", label: "All decisions", resource: DECISION_MODEL, filter: {} },
  ],
  menus: {
    decisions: { label: "Decisions", icon: "check" },
    "decisions.queue": { parent: "decisions", label: "Decisions", icon: "check", sequence: 20 },
    "decisions.inbox": { parent: "decisions.queue", label: "Inbox", icon: "check", route: "decisions.inbox", defaultResourceView: "decisions.inbox" },
    "decisions.waiting": { parent: "decisions.queue", label: "Waiting on me", icon: "check", route: "decisions.inbox", defaultResourceView: "decisions.waiting" },
    "decisions.all": { parent: "decisions.queue", label: "All decisions", icon: "check", route: "decisions.inbox", defaultResourceView: "decisions.all" },
  },
  // Independent waiting owners link back from the generic card.
  containers: {
    "decisions#origin": {},
    "record#activity": {
      "decisions.answers": {
        sequence: 40,
        content: decisionAnswerActivitySource,
      },
    },
  },
  i18n: { decisions: enDecisionsMessages },
  statusTones: { "needs-decision": "warning" },
});

export { attentionColumn, openDecisionFilter, useDecisionFieldMarks } from "./attention";

function useDecisionAnswerEntries(record: ActivityRecord, window: ActivityWindow): readonly ActivityEntry[] {
  const variables = React.useMemo(() => ({ model: record.model, id: record.id, limit: window.limit }),
    [record.id, record.model, window.limit]);
  const query = useAuthoredQuery(RecordDecisionAnswersDocument, variables, {
    enabled: Boolean(record.model && record.id),
    models: [DECISION_MODEL, "decisions.DecisionRecord"],
    records: [{ model: record.model, id: record.id }],
  });
  return React.useMemo(() => (query.data?.record_decision_answers ?? []).map((decision) => ({
    at: decision.answered_at ?? decision.created_at,
    kind: "decision" as const,
    actor: decision.answered_by?.display_name,
    title: decision.verdict_label || decision.kind_label,
    body: React.createElement(DecisionCard, { decision, selfId: record.id, compact: true }),
    reference: null,
  })), [query.data?.record_decision_answers, record.id]);
}

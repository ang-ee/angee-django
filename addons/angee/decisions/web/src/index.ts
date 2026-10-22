import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { enDecisionsMessages } from "./i18n";
import { DECISION_MODEL } from "./documents.console";
import { decisionConcernsGroup, recordDecisionsGroup } from "./related";

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
  // Independent waiting owners link back from the generic card; answered
  // decisions outside a run are Timeline lines opening the decision's page.
  containers: {
    "decisions#origin": {},
    "timeline#kinds": {
      // An open decision waits on someone: the warning tone, as on the card and the attention pill.
      "decisions.decision": { content: { label: "Decisions", icon: "check", tone: "warning", verb: "Open decision", target: { page: true } } },
    },
    // Related: a decision lists the records it concerns; every other record lists its decisions.
    "related#groups": { "decisions.decisions": recordDecisionsGroup },
    "decisions.Decision#groups": { "decisions.concerns": decisionConcernsGroup },
  },
  i18n: { decisions: enDecisionsMessages },
  statusTones: { "needs-decision": "warning" },
});

export { attentionColumn, openDecisionFilter, useDecisionFieldMarks } from "./attention";

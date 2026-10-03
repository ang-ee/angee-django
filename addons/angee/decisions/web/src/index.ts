import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";

import { enDecisionsMessages } from "./i18n";
import { DECISION_MODEL } from "./documents.console";

export { DECISION_MODEL } from "./documents.console";
export { decisionContent, useDecisionContent, type DecisionContentProps } from "./content";
export { DecisionsList, decisionRecordTab } from "./RecordDecisions";

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
  // Consumers present a decision kind (one per kind); waiting owners link back from it.
  containers: { "decisions#content": { unique: "key" }, "decisions#origin": {} },
  i18n: { decisions: enDecisionsMessages },
});

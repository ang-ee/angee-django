import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";

import { enDecisionsMessages } from "./i18n";

export { DECISION_MODEL } from "./documents.console";
export { decisionContent, useDecisionContent, type DecisionContentProps } from "./slots";
export { decisionRecordTab } from "./RecordDecisions";

export default defineBaseAddon({
  id: "decisions",
  routes: resourcePageRoutes(
    "decisions.inbox", "/decisions",
    lazyRouteComponent(() => import("./InboxPage"), "InboxPage"),
    "decisions.Decision",
  ),
  menus: [{ id: "decisions", label: "Decisions", icon: "check", route: "decisions.inbox" }],
  // Consumers present a decision kind (one per kind); waiting owners link back from it.
  containers: { "decisions#content": { unique: "key" }, "decisions#origin": {} },
  i18n: { decisions: enDecisionsMessages },
});

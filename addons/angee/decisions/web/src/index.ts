import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";

import { enDecisionsMessages } from "./i18n";

export { DECISION_CONTENT_SLOT, DECISION_ORIGIN_SLOT, decisionContent, useDecisionContent, type DecisionContentProps } from "./slots";

export default defineBaseAddon({
  id: "decisions",
  routes: resourcePageRoutes(
    "decisions.inbox", "/decisions",
    lazyRouteComponent(() => import("./InboxPage"), "InboxPage"),
    "decisions.Decision",
  ),
  menus: [{ id: "decisions", label: "Decisions", icon: "check", route: "decisions.inbox" }],
  i18n: { decisions: enDecisionsMessages },
});

import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { formViewRecordActionsSlot } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";

import { DecisionRecordActions } from "./DecisionRecordActions";
import { enDecisionsMessages } from "./i18n";
import { DECISION_MODEL } from "./resources";

export { DECISION_MODEL } from "./resources";
export { DecisionsList, RecordDecisions, decisionRecordTab } from "./RecordDecisions";

export default defineBaseAddon({
  id: "decisions",
  i18n: { decisions: enDecisionsMessages },
  routes: resourcePageRoutes("decisions.decisions", "/decisions", lazyRouteComponent(() => import("./DecisionsPage"), "DecisionsPage"), DECISION_MODEL),
  menus: [{ id: "decisions", label: "Decisions", icon: "check", route: "decisions.decisions" }],
  slots: [{ ...formViewRecordActionsSlot(DECISION_MODEL), id: "decisions.verbs",
    recordActionPlacement: "menu", requiredFields: ["revision", "is_open", "can_revisit", "permissions", "form_schema"],
    content: <DecisionRecordActions /> }],
});

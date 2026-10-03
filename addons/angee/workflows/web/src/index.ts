import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { RUN_MODEL } from "./documents.console";
import { WORKFLOW_MODEL } from "./catalogue/resources";
import { decisionRunOrigin, workflowsRunsTab } from "./contributions";
import { enWorkflowsMessages } from "./i18n";
import { TriggerCondition } from "./TriggerCondition";
import { TRIGGER_MODEL, TRIGGER_EVENT_MODEL } from "./triggers";

export { TRIGGER_MODEL, TRIGGER_EVENT_MODEL } from "./triggers";

export default defineBaseAddon({
  id: "workflows",
  routes: [
    ...resourcePageRoutes("workflows.runs", "/workflows/runs", lazyRouteComponent(() => import("./RunsPage"), "RunsPage"), RUN_MODEL),
    ...resourcePageRoutes("workflows.catalogue", "/workflows", lazyRouteComponent(() => import("./WorkflowsPage"), "WorkflowsPage"), WORKFLOW_MODEL),
    ...resourcePageRoutes("workflows.triggers", "/workflows/triggers", lazyRouteComponent(() => import("./TriggersPage"), "TriggersPage"), TRIGGER_MODEL),
    ...resourcePageRoutes("workflows.trigger-events", "/workflows/trigger-events", lazyRouteComponent(() => import("./TriggerEventsPage"), "TriggerEventsPage"), TRIGGER_EVENT_MODEL),
  ],
  menus: {
    // Decisions is workflows' human-review inbox; it lives under Workflows.
    workflows: { label: "Workflows", icon: "versions", include: ["decisions"] },
    "workflows.runs": { parent: "workflows", label: "Runs", icon: "activity", route: "workflows.runs" },
    "workflows.catalogue": { parent: "workflows", label: "Workflows", icon: "versions", route: "workflows.catalogue" },
    "workflows.triggers": { parent: "workflows", label: "Triggers", icon: "activity", route: "workflows.triggers" },
  },
  containers: {
    "record#aside": { "workflows.runs": workflowsRunsTab },
    "decisions#origin": { "workflows.run": decisionRunOrigin },
  },

  widgets: { "angee.workflows.condition": { read: TriggerCondition, edit: TriggerCondition } },
  i18n: { workflows: enWorkflowsMessages },
});

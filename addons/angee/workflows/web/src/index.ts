import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { RUN_MODEL } from "./documents.console";
import { WORKFLOW_MODEL } from "./catalogue/resources";
import { decisionRunOrigin, workflowsChatter } from "./contributions";
import { enWorkflowsMessages } from "./i18n";

export default defineBaseAddon({
  id: "workflows",
  routes: [
    ...resourcePageRoutes("workflows.runs", "/workflows/runs", lazyRouteComponent(() => import("./RunsPage"), "RunsPage"), RUN_MODEL),
    ...resourcePageRoutes("workflows.catalogue", "/workflows", lazyRouteComponent(() => import("./WorkflowsPage"), "WorkflowsPage"), WORKFLOW_MODEL),
  ],
  menus: [
    { id: "workflows", label: "Workflows", icon: "versions" },
    { id: "workflows.runs", parentId: "workflows", label: "Runs", icon: "activity", route: "workflows.runs" },
    { id: "workflows.catalogue", parentId: "workflows", label: "Workflows", icon: "versions", route: "workflows.catalogue" },
  ],
  chatter: [workflowsChatter],
  slots: [decisionRunOrigin],
  i18n: { workflows: enWorkflowsMessages },
});

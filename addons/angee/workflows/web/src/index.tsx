import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute } from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import {
  ArrowUpRight,
  CheckCircle2,
  GitBranch,
  Inbox,
  ListChecks,
  Network,
  PlayCircle,
  Send,
  Waypoints,
  XCircle,
} from "lucide-react";

import { enWorkflowsMessages } from "./i18n";

const WORKFLOWS_ID = "workflows";

const workflowsRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes(
    "workflows.workflows",
    "/workflows",
    lazyRouteComponent(() => import("./views/WorkflowsPage"), "WorkflowsPage"),
    "workflows.Workflow",
    { detailName: "workflows.workflow" },
  ),
  ...resourcePageRoutes(
    "workflows.runs",
    "/workflows/runs",
    lazyRouteComponent(() => import("./views/RunsPage"), "RunsPage"),
    "workflows.WorkflowRun",
    { detailName: "workflows.run" },
  ),
  {
    name: "workflows.inbox",
    path: "/workflows/inbox",
    layout: "console",
    component: lazyRouteComponent(() => import("./views/InboxPage"), "InboxPage"),
  },
];

const workflowsMenu: readonly BaseMenuItem[] = [
  {
    id: WORKFLOWS_ID,
    label: "Workflows",
    icon: "workflow",
    sidebar: true,
    children: [
      {
        id: "workflows.workflows",
        label: "Workflows",
        icon: "workflow",
        route: "workflows.workflows",
      },
      {
        id: "workflows.runs",
        label: "Runs",
        icon: "workflow-run",
        route: "workflows.runs",
      },
      {
        id: "workflows.inbox",
        label: "Inbox",
        icon: "workflow-inbox",
        route: "workflows.inbox",
      },
    ],
  },
];

const workflows = defineBaseAddon({
  id: WORKFLOWS_ID,
  routes: workflowsRoutes,
  menus: workflowsMenu,
  i18n: { workflows: enWorkflowsMessages },
  icons: {
    workflow: GitBranch,
    "workflow-canvas": Network,
    "workflow-run": PlayCircle,
    "workflow-inbox": Inbox,
    "workflow-trigger": ListChecks,
    "workflow-publish": Send,
    "workflow-cancel": XCircle,
    "workflow-approve": CheckCircle2,
    "workflow-reject": XCircle,
    "workflow-escalate": ArrowUpRight,
    "workflow-step": Waypoints,
  },
});

export default workflows;

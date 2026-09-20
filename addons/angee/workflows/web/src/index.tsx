import { defineBaseAddon, resourcePageRoutes, type BaseAddonRoute } from "@angee/app";
import { FORM_VIEW_RECORD_CHROME_SLOT, IMPLEMENTATION_DETAIL_SLOT, type BaseMenuItem } from "@angee/ui";
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
import { RunWorkflowMenu } from "./RunWorkflowMenu";
import { decisionContextWidgets } from "./views/DecisionContextWidgets";
import { WorkflowImplementationDetails } from "./views/WorkflowImplementationDetails";
export { WORKFLOW_DECISION_CONTENT_SLOT, WORKFLOW_TRIGGER_FORM_FIELDS_SLOT } from "./slots";

import { CHATTER_TAB_SEARCH_KEY } from "@angee/ui";
import { DECISION_SEARCH_KEY, WORKFLOW_RUN_SEARCH_KEY } from "./decision-navigation";
export {
  DECISION_SEARCH_KEY,
  decisionHref,
  decisionSearch,
  workflowSubjectActionSearch,
} from "./decision-navigation";

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
  ...resourcePageRoutes(
    "workflows.steps",
    "/workflows/steps",
    lazyRouteComponent(() => import("./views/StepsPage"), "StepsPage"),
    "workflows.Step",
    {
      detailName: "workflows.step",
      detailComponent: lazyRouteComponent(() => import("./views/StepDetail"), "StepDetail"),
    },
  ),
  ...resourcePageRoutes(
    "workflows.inbox",
    "/workflows/inbox",
    lazyRouteComponent(() => import("./views/InboxPage"), "InboxPage"),
    "workflows.Decision",
  ),
];

const workflowsMenu: readonly BaseMenuItem[] = [
  {
    id: WORKFLOWS_ID,
    label: "Workflows",
    icon: "workflow",
    children: [
      {
        id: "workflows.workflows",
        label: "Workflows",
        icon: "workflow",
        route: "workflows.workflows",
      },
      {
        id: "workflows.steps",
        label: "Steps",
        icon: "workflow-step",
        route: "workflows.steps",
      },
      {
        id: "workflows.runs",
        label: "Runs",
        icon: "workflow-run",
        route: "workflows.runs",
      },
      {
        id: "workflows.inbox",
        label: "Approvals",
        icon: "workflow-inbox",
        route: "workflows.inbox",
      },
    ],
  },
];

const workflows = defineBaseAddon({
  id: WORKFLOWS_ID,
  recordSearchKeys: [CHATTER_TAB_SEARCH_KEY, DECISION_SEARCH_KEY, WORKFLOW_RUN_SEARCH_KEY],
  routes: workflowsRoutes,
  menus: workflowsMenu,
  i18n: { workflows: enWorkflowsMessages },
  widgets: decisionContextWidgets,
  slots: [
    {
      slot: FORM_VIEW_RECORD_CHROME_SLOT,
      id: "workflows.run-workflow",
      sequence: 50,
      content: <RunWorkflowMenu />,
    },
    {
      slot: IMPLEMENTATION_DETAIL_SLOT,
      model: "workflows.Step",
      id: "workflows.step-implementation-details",
      content: <WorkflowImplementationDetails />,
    },
  ],
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
export {
  ApprovalTask,
  DecisionContextFields,
  DecisionField,
  DecisionReferenceAction,
  useInitialDecisionPeek,
} from "./views/ApprovalTask";
export type { ApprovalTaskProps, ApprovalVerdict, WorkflowDecisionContentComponent, WorkflowDecisionContentProps, WorkflowDecisionRecordReference } from "./views/ApprovalTask";
export { WorkflowApprovals } from "./views/WorkflowApprovals";
export type { WorkflowApprovalsProps } from "./views/WorkflowApprovals";
export { useWorkflowsT } from "./i18n";
export { workflowTriggerAssignmentForm } from "./views/WorkflowTriggersPanel";
export { WorkflowSubjectHistoryPane } from "./views/WorkflowSubjectHistoryPane";
export { useWorkflowSubjectActionResult } from "./useWorkflowSubjectActionResult";

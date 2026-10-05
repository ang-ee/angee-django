import {
  defineBaseAddon,
  resourcePageRoutes,
  type BaseAddonRoute,
} from "@angee/app";
import {
  Field,
  Group,
  type BaseMenuItem,
} from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import {
  ArchiveRestore,
  CalendarClock,
  CheckCircle2,
  GitBranch,
  Inbox,
  Kanban,
  LampDesk,
  Play,
  XCircle,
} from "lucide-react";
import { PROJECT_MODEL, TASK_MODEL } from "@angee/projects";
import { ShareAccessRailGroup } from "@angee/iam";

import { ProjectManagerAccessRole } from "./access-role";
import { enWorkMessages } from "./i18n";
import { QUEUE_MODEL } from "./resources";
import { StageStatusbar } from "./stage-statusbar";
import { taskWorkFormSection } from "./task-work";
import { TriageRecordActions } from "./triage-actions";

const workRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes(
    "work.queues",
    "/work/queues",
    lazyRouteComponent(() => import("./views/QueuesPage"), "QueuesPage"),
    QUEUE_MODEL,
  ),
  {
    name: "work.triage-hub",
    path: "/work/triage",
    layout: "console",
    component: lazyRouteComponent(
      () => import("./views/QueueHubPages"),
      "TriageHubPage",
    ),
  },
  {
    name: "work.boards-hub",
    path: "/work/boards",
    layout: "console",
    component: lazyRouteComponent(
      () => import("./views/QueueHubPages"),
      "BoardsHubPage",
    ),
  },
  {
    name: "work.cycles-hub",
    path: "/work/cycles",
    layout: "console",
    component: lazyRouteComponent(
      () => import("./views/QueueHubPages"),
      "CyclesHubPage",
    ),
  },
  {
    name: "work.board",
    path: "/work/queues/$queueId/board",
    layout: "console",
    menu: "work.boards-hub",
    component: lazyRouteComponent(
      () => import("./views/QueueBoardPage"),
      "QueueBoardPage",
    ),
  },
  {
    name: "work.triage",
    path: "/work/queues/$queueId/triage",
    layout: "console",
    menu: "work.triage-hub",
    component: lazyRouteComponent(
      () => import("./views/TriageInboxPage"),
      "TriageInboxPage",
    ),
  },
  {
    name: "work.cycles",
    path: "/work/queues/$queueId/cycles",
    layout: "console",
    menu: "work.cycles-hub",
    // Projection page (PipelinePage rule): a parameterized route must not
    // claim a resource — the collection href could never resolve at boot.
    component: lazyRouteComponent(
      () => import("./views/CyclesPage"),
      "CyclesPage",
    ),
  },
  {
    name: "work.cycle-board",
    path: "/work/queues/$queueId/cycles/$id",
    parent: "work.cycles",
    menu: "work.cycles-hub",
    component: lazyRouteComponent(
      () => import("./views/CycleBoardPage"),
      "CycleBoardPage",
    ),
  },
];

const workMenu: readonly BaseMenuItem[] = [
  {
    id: "work",
    label: "Work",
    icon: "work",
    children: [
      {
        id: "work.queues",
        label: "Queues",
        icon: "list-ordered",
        route: "work.queues",
      },
      {
        id: "work.triage-hub",
        label: "Triage",
        icon: "work-triage",
        route: "work.triage-hub",
      },
      {
        id: "work.boards-hub",
        label: "Boards",
        icon: "work-board",
        route: "work.boards-hub",
      },
      {
        id: "work.cycles-hub",
        label: "Cycles",
        icon: "work-cycle",
        route: "work.cycles-hub",
      },
    ],
  },
];

const work = defineBaseAddon({
  id: "work",
  routes: workRoutes,
  menus: workMenu,
  i18n: { work: enWorkMessages },
  widgets: { "work.stage": { read: StageStatusbar, edit: StageStatusbar } },
  containers: {
    [`${PROJECT_MODEL}#access-roles`]: {
      "work.manager": { content: ProjectManagerAccessRole },
    },
    [`${PROJECT_MODEL}#rail`]: {
      "work.people-rail": { sequence: 40, content: ShareAccessRailGroup },
    },
    [`${PROJECT_MODEL}#sections`]: {
      "work.project-team": { sequence: 40, content: <Group><Field name="team" /></Group> },
    },
    [`${TASK_MODEL}#sections`]: {
      "work.task-fields": { sequence: 40, content: taskWorkFormSection },
    },
    // Every triage verb is a menu verb: it joins the form's one Actions menu.
    [`${TASK_MODEL}#actions-menu`]: {
      "work.task-triage-actions": { sequence: 40, content: <TriageRecordActions /> },
    },
  },
  icons: {
    work: LampDesk,
    "work-board": Kanban,
    "work-cycle": CalendarClock,
    "work-triage": Inbox,
    "work-start": Play,
    "work-accept": CheckCircle2,
    "work-decline": XCircle,
    "work-snooze": CalendarClock,
    "work-duplicate": GitBranch,
    "work-cycle-close": ArchiveRestore,
  },
});

export { estimateLabel } from "./estimates";
export { CYCLE_MODEL, QUEUE_MODEL, STAGE_MODEL } from "./resources";
export { useQueueRecordTabs } from "./queue-record-tabs";
export { RemovedTasks, type RemovedTasksProps } from "./removed-tasks";
export default work;

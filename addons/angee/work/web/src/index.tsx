import {
  defineBaseAddon,
  resourcePageRoutes,
  type BaseAddonRoute,
} from "@angee/app";
import {
  formViewRecordActionsSlot,
  formViewSectionsSlot,
  type BaseMenuItem,
} from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import {
  ArchiveRestore,
  Briefcase,
  CalendarClock,
  CheckCircle2,
  GitBranch,
  Inbox,
  Kanban,
  XCircle,
} from "lucide-react";

import { enWorkMessages } from "./i18n";
import { QUEUE_MODEL } from "./resources";
import { taskWorkFormSection } from "./task-work";
import { TriageRecordActions } from "./triage-actions";

const TASK_MODEL = "projects.Task";

const workRoutes: readonly BaseAddonRoute[] = [
  ...resourcePageRoutes(
    "work.queues",
    "/work/queues",
    lazyRouteComponent(() => import("./views/QueuesPage"), "QueuesPage"),
    QUEUE_MODEL,
  ),
  {
    name: "work.board",
    path: "/work/queues/$queueId/board",
    layout: "console",
    menu: "work.queues",
    component: lazyRouteComponent(
      () => import("./views/QueueBoardPage"),
      "QueueBoardPage",
    ),
  },
  {
    name: "work.triage",
    path: "/work/queues/$queueId/triage",
    layout: "console",
    menu: "work.queues",
    component: lazyRouteComponent(
      () => import("./views/TriageInboxPage"),
      "TriageInboxPage",
    ),
  },
  {
    name: "work.cycles",
    path: "/work/queues/$queueId/cycles",
    layout: "console",
    menu: "work.queues",
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
    menu: "work.queues",
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
    icon: "work-queue",
    children: [
      {
        id: "work.queues",
        label: "Queues",
        icon: "work-queue",
        route: "work.queues",
      },
    ],
  },
];

const work = defineBaseAddon({
  id: "work",
  routes: workRoutes,
  menus: workMenu,
  i18n: { work: enWorkMessages },
  slots: [
    {
      ...formViewSectionsSlot(TASK_MODEL),
      id: "work.task-fields",
      sequence: 40,
      content: taskWorkFormSection,
    },
    {
      ...formViewRecordActionsSlot(TASK_MODEL),
      id: "work.task-triage-actions",
      sequence: 40,
      content: <TriageRecordActions />,
    },
  ],
  icons: {
    "work-queue": Briefcase,
    "work-board": Kanban,
    "work-cycle": CalendarClock,
    "work-triage": Inbox,
    "work-accept": CheckCircle2,
    "work-decline": XCircle,
    "work-snooze": CalendarClock,
    "work-duplicate": GitBranch,
    "work-cycle-close": ArchiveRestore,
  },
});

export { estimateLabel } from "./estimates";
export { CYCLE_MODEL, QUEUE_MODEL, STAGE_MODEL } from "./resources";
export default work;

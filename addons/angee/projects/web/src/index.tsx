import type { BaseAddonRoute } from "@angee/app";
import { defineBaseAddon, resourcePageRoutes } from "@angee/app";
import type { BaseMenuItem } from "@angee/ui";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Briefcase, ClipboardCheck, Kanban, ListChecks } from "lucide-react";

import { enProjectsMessages } from "./i18n";
import { PROJECT_MODEL, TASK_MODEL } from "./resources";

export {
  MILESTONE_MODEL,
  PARTICIPANT_MODEL,
  PROJECT_MODEL,
  TASK_MODEL,
} from "./resources";

const projectsRoutes: readonly BaseAddonRoute[] = [
  {
    name: "projects.my-work",
    path: "/projects/my-work",
    component: lazyRouteComponent(
      () => import("./views/MyWorkPage"),
      "MyWorkPage",
    ),
  },
  {
    name: "projects.board",
    path: "/projects/board",
    component: lazyRouteComponent(
      () => import("./views/TaskBoardPage"),
      "TaskBoardPage",
    ),
  },
  ...resourcePageRoutes(
    "projects.projects",
    "/projects",
    lazyRouteComponent(() => import("./views/ProjectsPage"), "ProjectsPage"),
    PROJECT_MODEL,
  ),
  ...resourcePageRoutes(
    "projects.tasks",
    "/projects/tasks",
    lazyRouteComponent(() => import("./views/TasksPage"), "TasksPage"),
    TASK_MODEL,
  ),
];

const projectsMenu: readonly BaseMenuItem[] = [
  {
    id: "projects",
    label: "Projects",
    icon: "projects",
    children: [
      {
        id: "projects.my-work",
        label: "My Work",
        icon: "my-work",
        route: "projects.my-work",
      },
      {
        id: "projects.projects",
        label: "Projects",
        icon: "projects",
        route: "projects.projects",
      },
      {
        id: "projects.tasks",
        label: "Tasks",
        icon: "project-task",
        route: "projects.tasks",
      },
      {
        id: "projects.board",
        label: "Task board",
        icon: "task-board",
        route: "projects.board",
      },
    ],
  },
];

const projects = defineBaseAddon({
  id: "projects",
  routes: projectsRoutes,
  menus: projectsMenu,
  i18n: { projects: enProjectsMessages },
  icons: {
    projects: Briefcase,
    "project-task": ListChecks,
    "task-board": Kanban,
    "my-work": ClipboardCheck,
  },
});

export default projects;

import type { BaseAddonRoute } from "@angee/app";
import { defineBaseAddon, resourcePageRoutes, type MenuDeclarations } from "@angee/app";
import { lazyRouteComponent } from "@tanstack/react-router";
import { Briefcase, ClipboardCheck, Kanban, ListChecks } from "lucide-react";

import { enProjectsMessages } from "./i18n";
import { PROJECT_MODEL, TASK_MODEL } from "./resources";
import { ProjectPhaseControl } from "./project-phase";
import { RecordTasksPane } from "./RecordTasksPane";

export {
  MILESTONE_MODEL,
  PARTICIPANT_MODEL,
  PROJECT_MODEL,
  TASK_MODEL,
} from "./resources";
/** The manager's phase dropdown for one project, composable onto a consumer's project page. */
export { ProjectPhaseControl } from "./project-phase";
export { projectListDeclaration, useProjectListDeclaration, projectGanttSpec, projectRecordTabs, projectRecordTabsFor, projectTimelineSpec, projectTimelineTab, useProjectFormDeclaration, type ProjectFormSelection, type ProjectTabSelection } from "./project-declarations";
export { taskRecordTabs, taskRecordTabsFor, useTaskListDeclaration } from "./task-declarations";
export { TASK_ACTION_FIELDS, offersTaskAction, useTaskFormDeclaration, type TaskFormSelection } from "./task-actions";
export { TaskBoardSurface, type TaskBoardSurfaceProps } from "./task-board-surface";

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

const projectsMenu: MenuDeclarations = {
  "projects": { label: "Projects", icon: "projects" },
  "projects.my-work": { label: "My Work", icon: "my-work", route: "projects.my-work", parent: "projects" },
  "projects.projects": { label: "Projects", icon: "projects", route: "projects.projects", parent: "projects" },
  "projects.tasks": { label: "Tasks", icon: "project-task", route: "projects.tasks", parent: "projects" },
  "projects.board": { label: "Task board", icon: "task-board", route: "projects.board", parent: "projects" },
};

const projects = defineBaseAddon({
  id: "projects",
  routes: projectsRoutes,
  menus: projectsMenu,
  i18n: { projects: enProjectsMessages },
  widgets: { "projects.phase": { read: ProjectPhaseControl, edit: ProjectPhaseControl } },
  containers: {
    // A task's line on the Timeline opens the Tasks tab.
    "timeline#kinds": {
      "projects.task": { content: { label: "Tasks", icon: "project-task", tone: "info", verb: "Open in Tasks", target: { tab: "projects.tasks", focus: "task" } } },
    },
    // The direct subject declaration determines which records take tasks.
    "record#aside": {
      "projects.tasks": {
        sequence: 20,
        content: {
          when: (context) => context.route?.recordEdges?.includes(TASK_MODEL) ?? false,
          label: "Tasks", icon: "project-task",
          render: (context) => <RecordTasksPane context={context} />,
        },
      },
    },
  },
  icons: {
    projects: Briefcase,
    "project-task": ListChecks,
    "task-board": Kanban,
    "my-work": ClipboardCheck,
  },
});

export default projects;

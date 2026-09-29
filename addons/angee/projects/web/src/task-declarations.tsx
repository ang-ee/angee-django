import {
  Column,
  DrawerResourceList,
  Facet,
  List,
  relationValueId,
  useRecordChromeContextMaybe,
  useResourceRecordHref,
  type ListProps,
  type RecordPanelContext,
  type RecordTabDescriptor,
} from "@angee/ui";
import * as React from "react";

import { enProjectsMessages, useProjectsT } from "./i18n";
import { TASK_MODEL } from "./resources";
import {
  useTaskFormDeclaration,
  useTaskRowActions,
  type TaskActionRow,
} from "./task-actions";

/** Task collection columns, facets, grouping and lifecycle row actions. */
export function useTaskListDeclaration(options: Partial<Omit<ListProps<TaskActionRow>, "children" | "resource">> = {}): React.ReactElement {
  const t = useProjectsT();
  const rowActions = useTaskRowActions<TaskActionRow>();
  return (
    <List<TaskActionRow>
      resource={TASK_MODEL}
      defaultGroup={{ field: "project" }}
      order={{ sort_order: "ASC" }}
      rowActions={rowActions}
      {...options}
    >
      <Facet field="project" label={t("common.project")} />
      <Facet field="visibility" label={t("common.visibility")} />
      <Facet field="assignee" label={t("common.assignee")} />
      <Column field="title" />
      <Column field="project.title" header={t("common.project")} />
      <Column field="visibility" header={t("common.visibility")} widget="statusBadge" />
      <Column field="status" header={t("common.status")} widget="statusBadge" />
      <Column field="assignee" header={t("common.assignee")} />
      <Column field="priority" header={t("common.priority")} />
      <Column field="due_date" header={t("common.dueDate")} />
    </List>
  );
}

export interface TaskManagementTabProps extends RecordPanelContext {
  /** Which relation scopes this collection to its saved parent. */
  relation: "project" | "parent";
  /** Main project plus consumer-declared descendant track ids. */
  projectIds?: readonly string[];
}

/** One task-management surface for a project or a task's subtasks. */
export function TaskManagementTab({ recordId, relation, projectIds }: TaskManagementTabProps): React.ReactElement {
  const record = useRecordChromeContextMaybe();
  const recordHref = useResourceRecordHref(TASK_MODEL);
  const parentProject = relationValueId(record?.record?.project);
  const createDefaults = relation === "project"
    ? { project: recordId }
    : { parent: recordId, ...(parentProject ? { project: parentProject } : {}) };
  const baseFilter = relation === "project"
    ? { project: { inList: [...(projectIds ?? [recordId])] } }
    : { parent: { exact: recordId } };
  const list = useTaskListDeclaration({
    scope: "local",
    baseFilter,
    order: { [relation === "project" ? "sort_order" : "sub_sort_order"]: "ASC" },
    rowHref: recordHref ? (row) => recordHref(row.id, row) ?? "" : undefined,
    laneSource: { field: "assignee" },
    gantt: { start: "due_date", end: "due_date", label: "title" },
  });
  const form = useTaskFormDeclaration();
  return <DrawerResourceList<TaskActionRow> resource={TASK_MODEL} baseFilter={baseFilter} createDefaults={createDefaults}>
    {list}
    {form}
  </DrawerResourceList>;
}

/** Standard saved-task panels, reusable on any task route. */
export function taskRecordTabsFor(tabs: readonly "subtasks"[] = ["subtasks"]): readonly RecordTabDescriptor[] {
  return [
  { id: "subtasks", label: { namespace: "projects", key: "task.tabs.subtasks", fallback: enProjectsMessages["task.tabs.subtasks"] }, render: (context) => <TaskManagementTab {...context} relation="parent" /> },
  ].filter(({ id }) => tabs.includes(id as "subtasks"));
}

export const taskRecordTabs = taskRecordTabsFor();

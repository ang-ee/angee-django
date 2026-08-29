import {
  Column,
  Facet,
  List,
  ResourceList,
  useRouteHref,
  type RecordPanelContext,
  type RecordTabDescriptor,
} from "@angee/ui";
import * as React from "react";

import { useProjectsT } from "../i18n";
import { TASK_MODEL } from "../resources";
import {
  useTaskFormDeclaration,
  useTaskRowActions,
  type TaskActionRow,
} from "../task-actions";

/** All accessible tasks with project/status/assignee grouping and routed records. */
export function TasksPage(): React.ReactElement {
  const t = useProjectsT();
  const rowActions = useTaskRowActions<TaskActionRow>();
  const form = useTaskFormDeclaration();
  const recordTabs = React.useMemo<readonly RecordTabDescriptor[]>(
    () => [
      {
        id: "subtasks",
        label: t("task.tabs.subtasks"),
        render: (context) => <TaskSubtasksTab {...context} />,
      },
    ],
    [t],
  );

  return (
    <ResourceList<TaskActionRow>
      resource={TASK_MODEL}
      placement="inline"
      routed
      recordTabs={recordTabs}
    >
      <List<TaskActionRow>
        resource={TASK_MODEL}
        defaultGroup={{ field: "project" }}
        order={{ sort_order: "ASC" }}
        rowActions={rowActions}
      >
        <Facet field="project" label={t("common.project")} labelField="title" />
        <Facet field="assignee" label={t("common.assignee")} />
        <Column field="title" />
        <Column field="project.title" header={t("common.project")} />
        <Column field="status" header={t("common.status")} widget="statusBadge" />
        <Column field="assignee" header={t("common.assignee")} />
        <Column field="priority" header={t("common.priority")} />
        <Column field="due_date" header={t("common.dueDate")} />
        <Column field="sort_order" header={t("common.order")} />
      </List>
      {form}
    </ResourceList>
  );
}

function TaskSubtasksTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useProjectsT();
  const routeHref = useRouteHref();
  const rowActions = useTaskRowActions<TaskActionRow>();
  return (
    <List<TaskActionRow>
      resource={TASK_MODEL}
      scope="local"
      baseFilter={{ parent: { exact: recordId } }}
      order={{ sub_sort_order: "ASC" }}
      rowActions={rowActions}
      rowHref={(row) => routeHref("projects.tasks.record", { id: row.id })}
      emptyContent={t("task.empty.subtasks")}
    >
      <Column field="title" />
      <Column field="status" widget="statusBadge" />
      <Column field="assignee" />
      <Column field="priority" />
      <Column field="due_date" />
      <Column field="sub_sort_order" header={t("common.subtaskOrder")} />
    </List>
  );
}

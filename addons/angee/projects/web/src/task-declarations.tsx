import {
  Column,
  Facet,
  List,
  useResourceRecordHref,
  type RecordPanelContext,
  type RecordTabDescriptor,
} from "@angee/ui";
import * as React from "react";
import { useNavigate } from "@tanstack/react-router";

import { useProjectsT } from "./i18n";
import { TASK_MODEL } from "./resources";
import {
  useTaskRowActions,
  type TaskActionRow,
} from "./task-actions";

/** Task collection columns, facets, grouping and lifecycle row actions. */
export function useTaskListDeclaration(): React.ReactElement {
  const t = useProjectsT();
  const rowActions = useTaskRowActions<TaskActionRow>();
  return (
    <List<TaskActionRow>
      resource={TASK_MODEL}
      defaultGroup={{ field: "project" }}
      order={{ sort_order: "ASC" }}
      rowActions={rowActions}
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

function TaskSubtasksTab({ recordId }: RecordPanelContext): React.ReactElement {
  const t = useProjectsT();
  const recordHref = useResourceRecordHref(TASK_MODEL);
  const navigate = useNavigate();
  const rowActions = useTaskRowActions<TaskActionRow>();
  return (
    <List<TaskActionRow>
      resource={TASK_MODEL}
      scope="local"
      baseFilter={{ parent: { exact: recordId } }}
      order={{ sub_sort_order: "ASC" }}
      rowActions={rowActions}
      onRowClick={recordHref ? (row) => {
        const to = recordHref(row.id);
        if (to) void navigate({ to });
      } : undefined}
      emptyContent={t("task.empty.subtasks")}
    >
      <Column field="title" />
      <Column field="status" widget="statusBadge" />
      <Column field="assignee" />
      <Column field="priority" />
      <Column field="due_date" />
    </List>
  );
}

function SubtasksLabel(): React.ReactElement {
  const t = useProjectsT();
  return <>{t("task.tabs.subtasks")}</>;
}

/** Standard saved-task panels, reusable on any task route. */
export const taskRecordTabs: readonly RecordTabDescriptor[] = [
  { id: "subtasks", label: <SubtasksLabel />, render: (context) => <TaskSubtasksTab {...context} /> },
];

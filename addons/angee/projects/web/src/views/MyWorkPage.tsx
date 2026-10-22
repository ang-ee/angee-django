import {
  Column,
  List,
  useRuntimeAuth,
} from "@angee/ui";
import * as React from "react";

import { useProjectsT } from "../i18n";
import { TASK_MODEL } from "../resources";
import { useTaskRowActions, useTaskRowHref, type TaskActionRow } from "../task-actions";

/**
 * The personal projection: the actor's open tasks are the page's one list, owning
 * route state, favourites and Share. Tasks about a record are assigned tasks too,
 * so the list carries every next step the actor owns.
 */
export function MyWorkPage(): React.ReactElement {
  const t = useProjectsT();
  const userId = useRuntimeAuth().user?.id;
  const rowHref = useTaskRowHref();
  const taskActions = useTaskRowActions<TaskActionRow>();
  const taskFilter = React.useMemo(
    () => userId
      ? { assignee: { exact: userId }, status: { exact: "OPEN" } }
      : { id: { inList: [] } },
    [userId],
  );

  return (
    <List<TaskActionRow>
      resource={TASK_MODEL}
      baseFilter={taskFilter}
      order={{ due_date: "ASC", sort_order: "ASC" }}
      rowActions={taskActions}
      rowHref={rowHref}
      emptyContent={t("myWork.empty.tasks")}
    >
      <Column field="title" />
      <Column field="project.title" header={t("common.project")} />
      <Column field="priority" header={t("common.priority")} />
      <Column field="due_date" header={t("common.dueDate")} />
    </List>
  );
}

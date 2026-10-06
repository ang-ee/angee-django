import { ActivityAgendaPane } from "@angee/messaging";
import {
  Column,
  List,
  usePrimaryPane,
  useResourceRecordHref,
  useRuntimeAuth,
} from "@angee/ui";
import * as React from "react";

import { useProjectsT } from "../i18n";
import { TASK_MODEL } from "../resources";
import { useTaskRowActions, type TaskActionRow } from "../task-actions";

/**
 * The personal projection: the actor's open tasks are the page's one list, owning
 * route state, favourites and Share; messaging's Activities due sits beside it in
 * the console's primary (left) pane.
 */
export function MyWorkPage(): React.ReactElement {
  const t = useProjectsT();
  const userId = useRuntimeAuth().user?.id;
  const recordHref = useResourceRecordHref(TASK_MODEL);
  const taskActions = useTaskRowActions<TaskActionRow>();
  const agenda = React.useMemo(() => <ActivityAgendaPane />, []);
  usePrimaryPane(agenda);
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
      rowHref={recordHref ? (row) => recordHref(row.id, row) ?? "" : undefined}
      emptyContent={t("myWork.empty.tasks")}
    >
      <Column field="title" />
      <Column field="project.title" header={t("common.project")} />
      <Column field="priority" header={t("common.priority")} />
      <Column field="due_date" header={t("common.dueDate")} />
    </List>
  );
}

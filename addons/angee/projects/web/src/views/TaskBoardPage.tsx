import {
  Column,
  List,
} from "@angee/ui";
import * as React from "react";

import { useProjectsT } from "../i18n";
import { TaskBoardSurface } from "../task-board-surface";
import { TASK_MODEL } from "../resources";
import {
  useTaskRowActions,
  type TaskActionRow,
} from "../task-actions";

/** Personal-floor chore chart: open tasks in assignee lanes, ranked in-lane. */
export function TaskBoardPage(): React.ReactElement {
  const t = useProjectsT();
  const rowActions = useTaskRowActions<TaskActionRow>();

  return (
    <TaskBoardSurface<TaskActionRow>>
      <List<TaskActionRow>
        resource={TASK_MODEL}
        defaultView="board"
        baseFilter={{ status: { exact: "OPEN" } }}
        order={{ sort_order: "ASC" }}
        laneSource={{ field: "assignee", rankField: "sort_order" }}
        rowActions={rowActions}
        emptyContent={{
          icon: "task-board",
          title: t("board.empty.title"),
          description: t("board.empty.description"),
        }}
      >
        <Column field="title" />
        <Column field="project.title" header={t("common.project")} />
        <Column field="priority" header={t("common.priority")} />
        <Column field="due_date" header={t("common.dueDate")} />
      </List>
    </TaskBoardSurface>
  );
}

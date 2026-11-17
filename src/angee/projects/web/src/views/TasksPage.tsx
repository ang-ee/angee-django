import { ResourceList } from "@angee/ui";
import * as React from "react";

import { taskForm } from "../record-forms";
import { TASK_MODEL } from "../resources";
import { type TaskActionRow } from "../task-actions";
import { useTaskListDeclaration } from "../task-declarations";

/** All accessible tasks with the shared declarations and routed records. */
export function TasksPage(): React.ReactElement {
  const list = useTaskListDeclaration();
  return <ResourceList<TaskActionRow>
    resource={TASK_MODEL} placement="inline" routed form={taskForm}
    defaultFilter={{ NOT: { status: { exact: "DROPPED" } } }}
  >
    {list}
  </ResourceList>;
}

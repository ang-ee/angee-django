import { ResourceList } from "@angee/ui";
import * as React from "react";

import { TASK_MODEL } from "../resources";
import { useTaskFormDeclaration, type TaskActionRow } from "../task-actions";
import { taskRecordTabs, useTaskListDeclaration } from "../task-declarations";

/** All accessible tasks with the shared declarations and routed records. */
export function TasksPage(): React.ReactElement {
  const list = useTaskListDeclaration();
  const form = useTaskFormDeclaration();
  return <ResourceList<TaskActionRow>
    resource={TASK_MODEL} placement="inline" routed recordTabs={taskRecordTabs}
    defaultFilter={{ NOT: { status: { exact: "DROPPED" } } }}
  >
    {list}
    {form}
  </ResourceList>;
}

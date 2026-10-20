import { extractActionOutcome, useAuthoredQuery } from "@angee/refine";
import { RemovedList, useActionResultRun, useAuthoredResourceMutation } from "@angee/ui";
import * as React from "react";
import { TASK_MODEL } from "@angee/projects";

import { RemovedTasksDocument, RestoreTaskDocument } from "./documents";

export interface RemovedTasksProps {
  /** List the queue's removed tasks. */
  queue?: string;
  /** List the removed child tasks of one parent task, e.g. a request's questions. */
  parent?: string;
  className?: string;
}

type RemovedTaskRow = NonNullable<ReturnType<typeof useAuthoredQuery<typeof RemovedTasksDocument>>["data"]>["removed_tasks"][number];

/**
 * "Removed (n)" for removed tasks, with Restore. The server lists only tasks
 * the reader may restore (the inherited queue's writers and administrators),
 * so everyone else sees nothing; a restored task returns to its earlier stage.
 */
export function RemovedTasks({ queue, parent, className }: RemovedTasksProps): React.ReactElement | null {
  const variables = React.useMemo(() => ({ queue: queue ?? null, parent: parent ?? null }), [queue, parent]);
  const removed = useAuthoredQuery(RemovedTasksDocument, variables, {
    enabled: Boolean(queue || parent),
    models: [TASK_MODEL],
  });
  const settle = useActionResultRun();
  const [restore, restoreState] = useAuthoredResourceMutation(RestoreTaskDocument, {
    invalidateModels: [TASK_MODEL],
    shouldInvalidate: (data) => data?.restore_task.ok === true,
  });
  const rows = removed.data?.removed_tasks ?? [];
  return (
    <RemovedList<RemovedTaskRow>
      rows={rows}
      error={removed.error}
      label={(row) => row.title}
      stamps={(row) => ({ trashedByLabel: row.removed_by_label, trashReason: row.removal_reason })}
      canRestore={() => true}
      busy={restoreState.fetching}
      restore={(row) => settle(async () => extractActionOutcome(
        await restore({ task: row.id, expected_revision: row.revision }),
        "restore_task",
      ))}
      className={className}
    />
  );
}

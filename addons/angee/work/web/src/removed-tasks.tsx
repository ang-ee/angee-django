import { extractActionOutcome, useAuthoredQuery } from "@angee/refine";
import {
  ErrorBanner,
  RemovedDisclosure,
  RemovedItem,
  useActionResultRun,
  useAuthoredResourceMutation,
} from "@angee/ui";
import * as React from "react";
import { TASK_MODEL } from "@angee/projects";

import { RemovedTasksDocument, RestoreTaskDocument } from "./documents";
import { useWorkT } from "./i18n";

export interface RemovedTasksProps {
  /** List the queue's removed tasks. */
  queue?: string;
  /** List the removed child tasks of one parent task, e.g. a request's questions. */
  parent?: string;
  className?: string;
}

/**
 * "Removed (n)" for removed tasks, with Restore. The server lists only tasks
 * the reader may restore (the inherited queue's writers and administrators),
 * so everyone else sees nothing; a restored task returns to its earlier stage.
 */
export function RemovedTasks({ queue, parent, className }: RemovedTasksProps): React.ReactElement | null {
  const t = useWorkT();
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
  if (removed.error) return <ErrorBanner description={t("removed.loadFailed")} />;
  const rows = removed.data?.removed_tasks ?? [];
  return (
    <RemovedDisclosure count={rows.length} className={className}>
      {rows.map((row) => (
        <RemovedItem
          key={row.id}
          label={row.title}
          trashedByLabel={row.removed_by_label}
          trashReason={row.removal_reason}
          busy={restoreState.fetching}
          onRestore={() => {
            void settle(async () => extractActionOutcome(
              await restore({ task: row.id, expected_revision: row.revision }),
              "restore_task",
            ));
          }}
        />
      ))}
    </RemovedDisclosure>
  );
}

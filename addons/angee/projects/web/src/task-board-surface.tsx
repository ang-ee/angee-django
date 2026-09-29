import { ResourceList, useResourceRecordHref } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { TASK_MODEL } from "./resources";
import { useTaskFormDeclaration, type TaskActionRow } from "./task-actions";

export interface TaskBoardSurfaceProps<TRow extends TaskActionRow> {
  children: React.ReactNode;
  createDefaults?: Record<string, unknown>;
}

/**
 * Shared task-board record composition: task creation opens in the board drawer,
 * while selecting an existing card follows the canonical task detail route.
 */
export function TaskBoardSurface<TRow extends TaskActionRow>({
  children,
  createDefaults,
}: TaskBoardSurfaceProps<TRow>): React.ReactElement {
  const navigate = useNavigate();
  const taskHref = useResourceRecordHref(TASK_MODEL);
  const [creating, setCreating] = React.useState(false);
  const form = useTaskFormDeclaration();
  const select = React.useCallback(
    (id: string | null) => {
      if (id === null) {
        setCreating(true);
        return;
      }
      setCreating(false);
      const to = taskHref?.(id);
      if (to) void navigate({ to });
    },
    [navigate, taskHref],
  );

  return (
    <ResourceList<TRow>
      resource={TASK_MODEL}
      placement="drawer"
      creating={creating}
      onSelect={select}
      onClose={() => setCreating(false)}
      createDefaults={createDefaults}
      rowHref={taskHref ? (row) => taskHref(row.id, row) ?? "" : undefined}
    >
      {children}
      {form}
    </ResourceList>
  );
}

import { ResourceList, useRouteHref } from "@angee/ui";
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
  const routeHref = useRouteHref();
  const [creating, setCreating] = React.useState(false);
  const form = useTaskFormDeclaration();
  const taskHref = React.useCallback(
    (id: string) => routeHref("projects.tasks.record", { id }),
    [routeHref],
  );
  const select = React.useCallback(
    (id: string | null) => {
      if (id === null) {
        setCreating(true);
        return;
      }
      setCreating(false);
      void navigate({ to: taskHref(id) });
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
      rowHref={(row) => taskHref(row.id)}
    >
      {children}
      {form}
    </ResourceList>
  );
}

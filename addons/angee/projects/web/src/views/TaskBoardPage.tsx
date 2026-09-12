import {
  Column,
  List,
  ResourceList,
  useRouteHref,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { useProjectsT } from "../i18n";
import { TASK_MODEL } from "../resources";
import {
  useTaskFormDeclaration,
  useTaskRowActions,
  type TaskActionRow,
} from "../task-actions";

/** Personal-floor chore chart: open tasks in assignee lanes, ranked in-lane. */
export function TaskBoardPage(): React.ReactElement {
  const t = useProjectsT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const [creating, setCreating] = React.useState(false);
  const rowActions = useTaskRowActions<TaskActionRow>();
  const form = useTaskFormDeclaration();
  // Memoized, not inline: these are identity-compared downstream, so a fresh
  // object on every render re-runs the collection surface's effects and its
  // grouped-scope work for a board that has not changed.
  const baseFilter = React.useMemo(() => ({ status: { exact: "OPEN" } }), []);
  const order = React.useMemo(() => ({ sort_order: "ASC" as const }), []);
  const laneSource = React.useMemo(
    () => ({ field: "assignee", rankField: "sort_order" }),
    [],
  );
  const select = React.useCallback(
    (id: string | null) => {
      if (id === null) {
        setCreating(true);
        return;
      }
      setCreating(false);
      void navigate({ to: routeHref("projects.tasks.record", { id }) });
    },
    [navigate, routeHref],
  );

  return (
    <ResourceList<TaskActionRow>
      resource={TASK_MODEL}
      placement="drawer"
      creating={creating}
      onSelect={select}
      onClose={() => setCreating(false)}
    >
      <List<TaskActionRow>
        resource={TASK_MODEL}
        defaultView="board"
        baseFilter={baseFilter}
        order={order}
        laneSource={laneSource}
        rowActions={rowActions}
        emptyContent={{
          icon: "task-board",
          title: t("board.empty.title"),
          description: t("board.empty.description"),
        }}
      >
        <Column field="title" />
        <Column field="project.title" header={t("common.project")} />
        <Column field="priority" header={t("common.priority")} widget="priority" />
        <Column field="due_date" header={t("common.dueDate")} />
      </List>
      {form}
    </ResourceList>
  );
}

import { TASK_MODEL, TaskBoardSurface } from "@angee/projects";
import {
  Column,
  ErrorBanner,
  List,
  Page,
  PageBody,
  PageHeader,
  useRouteParam,
} from "@angee/ui";
import * as React from "react";

import { useCycleContext, useQueueContext } from "../context";
import { CycleCloseControl } from "../cycle-actions";
import { useWorkT } from "../i18n";
import { queueStageFilters } from "../stage-filters";
import { WorkTaskCard, type WorkTaskRow } from "../task-work";

/** One cycle's queue-stage board; work still ranks by task.sort_order. */
export function CycleBoardPage(): React.ReactElement {
  const queueId = useRouteParam("queueId") ?? "";
  const id = useRouteParam("id") ?? "";
  const t = useWorkT();
  const queue = useQueueContext(queueId);
  const cycle = useCycleContext(id);
  const queueFacts = queue.data?.work_queues_by_pk;
  const cycleFacts = cycle.data?.work_cycles_by_pk;
  const title = cycleFacts?.name ?? id;
  // Memoized, not inline: these are identity-compared downstream, so a fresh
  // object on every render re-runs the collection surface's effects and its
  // grouped-scope work for a board that has not changed.
  const createDefaults = React.useMemo(
    () => ({ queue: queueId, cycle: id }),
    [queueId, id],
  );
  const baseFilter = React.useMemo(
    () => ({ queue: { exact: queueId }, cycle: { exact: id } }),
    [queueId, id],
  );
  const laneSource = React.useMemo(
    () => ({
      field: "stage",
      rankField: "sort_order",
      filters: queueStageFilters(queueId),
      sorters: [{ field: "position", order: "asc" as const }],
    }),
    [queueId],
  );

  return (
    <Page>
      <PageHeader
        title={t("cycle.board.title", { cycle: title })}
        description={t("cycle.board.description")}
        actions={
          cycleFacts ? (
            <CycleCloseControl
              cycle={{
                id: cycleFacts.id,
                starts_on: cycleFacts.starts_on,
                completed_at: cycleFacts.completed_at,
              }}
            />
          ) : null
        }
      />
      <PageBody gutter="none" scroll="hidden" className="flex flex-col">
        {queue.error || cycle.error ? (
          <ErrorBanner description={(queue.error ?? cycle.error)?.message} />
        ) : null}
        <TaskBoardSurface<WorkTaskRow> createDefaults={createDefaults}>
          <List<WorkTaskRow>
            resource={TASK_MODEL}
            defaultView="board"
            // System-staged rows never render: lane filters exclude
            // triage/duplicate — `stage` is an ID comparison on the wire.
            baseFilter={baseFilter}
            order={{ sort_order: "ASC" }}
            laneSource={laneSource}
            renderCard={(task) => (
              <WorkTaskCard task={task} estimateScale={queueFacts?.estimate_scale} />
            )}
            emptyContent={{
              icon: "work-cycle",
              title: t("board.empty.title"),
              description: t("board.empty.description"),
            }}
          >
            <Column field="work_key" header={t("common.key")} />
            <Column field="title" />
            <Column field="estimate" header={t("common.estimate")} />
            <Column field="assignee" />
            <Column field="priority" widget="priority" />
            <Column field="due_date" />
          </List>
        </TaskBoardSurface>
      </PageBody>
    </Page>
  );
}

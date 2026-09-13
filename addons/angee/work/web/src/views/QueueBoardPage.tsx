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

import { useQueueContext } from "../context";
import { useWorkT } from "../i18n";
import { queueStageFilters } from "../stage-filters";
import { WorkTaskCard, type WorkTaskRow } from "../task-work";

/** Queue projection: stage lanes, sort_order rank, lane quick-create, task deep-links. */
export function QueueBoardPage(): React.ReactElement {
  const queueId = useRouteParam("queueId") ?? "";
  const t = useWorkT();
  const queue = useQueueContext(queueId);
  const name = queue.data?.work_queues_by_pk?.name ?? queueId;
  const scale = queue.data?.work_queues_by_pk?.estimate_scale;
  // Memoized, not inline: these are identity-compared downstream, so a fresh
  // object on every render re-runs the collection surface's effects and its
  // grouped-scope work for a board that has not changed.
  const createDefaults = React.useMemo(() => ({ queue: queueId }), [queueId]);
  const baseFilter = React.useMemo(
    () => ({ queue: { exact: queueId } }),
    [queueId],
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
        title={t("board.title", { queue: name })}
        description={t("board.description")}
      />
      <PageBody gutter="none" scroll="hidden" className="flex flex-col">
        {queue.error ? <ErrorBanner description={queue.error.message} /> : null}
        <TaskBoardSurface<WorkTaskRow> createDefaults={createDefaults}>
          <List<WorkTaskRow>
            resource={TASK_MODEL}
            defaultView="board"
            // System-staged rows never render: the lanes come from
            // queueStageFilters, which excludes triage/duplicate stages —
            // `stage` is an ID comparison on the wire, so a nested
            // stage.category filter is not expressible here.
            baseFilter={baseFilter}
            order={{ sort_order: "ASC" }}
            laneSource={laneSource}
            renderCard={(task) => <WorkTaskCard task={task} estimateScale={scale} />}
            emptyContent={{
              icon: "work-board",
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

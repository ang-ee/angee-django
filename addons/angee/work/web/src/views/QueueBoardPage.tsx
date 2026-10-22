import { TASK_MODEL, TaskBoardSurface } from "@angee/projects";
import { Column, ErrorBanner, List, useBreadcrumbLeafLabel, useRouteParam } from "@angee/ui";
import * as React from "react";

import { useQueueContext } from "../context";
import { estimateLabel } from "../estimates";
import { useWorkT } from "../i18n";
import { QueueRecordPage } from "../queue-page";
import { QueueSettingsLink } from "../queue-settings-link";
import { RemovedTasks } from "../removed-tasks";
import { queueStageFilters } from "../stage-filters";
import type { WorkTaskRow } from "../task-work";

/** Queue projection: stage lanes, sort_order rank, lane quick-create, task deep-links. */
export function QueueBoardPage(): React.ReactElement {
  const queueId = useRouteParam("queueId") ?? "";
  const t = useWorkT();
  const queue = useQueueContext(queueId);
  const name = queue.data?.work_queues_by_pk?.name;
  const scale = queue.data?.work_queues_by_pk?.estimate_scale;
  // The Boards hub's record page: the queue names the crumb after the hub.
  useBreadcrumbLeafLabel(name);
  return (
    <QueueRecordPage
      eyebrow={t("queue.eyebrow.board")}
      title={name ?? t("queue.loading")}
      description={t("board.description")}
      verbs={<QueueSettingsLink queueId={queueId} />}
      gutter="none"
      scroll="hidden"
    >
      {queue.error ? <ErrorBanner description={queue.error.message} /> : null}
      <RemovedTasks queue={queueId} className="px-4 pt-2" />
      <TaskBoardSurface<WorkTaskRow> createDefaults={{ queue: queueId }}>
        <List<WorkTaskRow>
          resource={TASK_MODEL}
          defaultView="board"
          // System-staged rows never render: the lanes come from
          // queueStageFilters, which excludes triage/duplicate stages —
          // `stage` is an ID comparison on the wire, so a nested
          // stage.category filter is not expressible here.
          baseFilter={{ queue: { exact: queueId } }}
          order={{ sort_order: "ASC" }}
          laneSource={{
            field: "stage",
            rankField: "sort_order",
            filters: queueStageFilters(queueId),
            sorters: [{ field: "position", order: "asc" }],
          }}
          emptyContent={{
            icon: "work-board",
            title: t("board.empty.title"),
            description: t("board.empty.description"),
          }}
        >
          {/* The declared columns are the board card: the title leads, the work key and the
              queue's own estimate vocabulary follow. */}
          <Column field="title" />
          <Column field="work_key" header={t("common.key")} />
          <Column field="estimate" header={t("common.estimate")} render={(row) => estimateLabel(row.estimate, scale, t)} />
          <Column field="priority" />
          <Column field="due_date" />
        </List>
      </TaskBoardSurface>
    </QueueRecordPage>
  );
}

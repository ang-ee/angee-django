import { TASK_MODEL, TaskBoardSurface } from "@angee/projects";
import { Column, ErrorBanner, List, useBreadcrumbLeafLabel, useRouteParam } from "@angee/ui";
import * as React from "react";

import { useCycleContext, useQueueContext } from "../context";
import { CycleCloseControl } from "../cycle-actions";
import { estimateLabel } from "../estimates";
import { useWorkT } from "../i18n";
import { QueueRecordPage } from "../queue-page";
import { queueStageFilters } from "../stage-filters";
import type { WorkTaskRow } from "../task-work";

/** One cycle's queue-stage board; work still ranks by task.sort_order. */
export function CycleBoardPage(): React.ReactElement {
  const queueId = useRouteParam("queueId") ?? "";
  const id = useRouteParam("id") ?? "";
  const t = useWorkT();
  const queue = useQueueContext(queueId);
  const cycle = useCycleContext(id);
  const queueFacts = queue.data?.work_queues_by_pk;
  const cycleFacts = cycle.data?.work_cycles_by_pk;
  // The Cycles hub's record page: the cycle names the crumb after its queue.
  useBreadcrumbLeafLabel(cycleFacts?.name);
  return (
    <QueueRecordPage
      eyebrow={queueFacts?.name ? t("cycle.eyebrow", { queue: queueFacts.name }) : t("cycle.eyebrowBare")}
      title={cycleFacts?.name ?? t("cycle.loading")}
      description={t("cycle.board.description")}
      verbs={cycleFacts ? (
        <CycleCloseControl
          cycle={{
            id: cycleFacts.id,
            starts_on: cycleFacts.starts_on,
            completed_at: cycleFacts.completed_at,
          }}
        />
      ) : null}
      gutter="none"
      scroll="hidden"
    >
      {queue.error || cycle.error ? (
        <ErrorBanner description={(queue.error ?? cycle.error)?.message} />
      ) : null}
      <TaskBoardSurface<WorkTaskRow> createDefaults={{ queue: queueId, cycle: id }}>
        <List<WorkTaskRow>
          resource={TASK_MODEL}
          defaultView="board"
          // System-staged rows never render: lane filters exclude
          // triage/duplicate — `stage` is an ID comparison on the wire.
          baseFilter={{
            queue: { exact: queueId },
            cycle: { exact: id },
          }}
          order={{ sort_order: "ASC" }}
          laneSource={{
            field: "stage",
            rankField: "sort_order",
            filters: queueStageFilters(queueId),
            sorters: [{ field: "position", order: "asc" }],
          }}
          emptyContent={{
            icon: "work-cycle",
            title: t("board.empty.title"),
            description: t("board.empty.description"),
          }}
        >
          <Column field="title" />
          <Column field="work_key" header={t("common.key")} />
          <Column field="estimate" header={t("common.estimate")} render={(row) => estimateLabel(row.estimate, queueFacts?.estimate_scale, t)} />
          <Column field="priority" />
          <Column field="due_date" />
        </List>
      </TaskBoardSurface>
    </QueueRecordPage>
  );
}

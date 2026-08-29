import { useTaskFormDeclaration } from "@angee/projects";
import {
  Column,
  ErrorBanner,
  List,
  Page,
  PageBody,
  PageHeader,
  ResourceList,
  useRouteHref,
} from "@angee/ui";
import { useNavigate, useParams } from "@tanstack/react-router";
import * as React from "react";

import { useQueueContext } from "../context";
import { useWorkT } from "../i18n";
import { queueStageFilters } from "../stage-filters";
import { WorkTaskCard, type WorkTaskRow } from "../task-work";

const TASK_MODEL = "projects.Task";

/** Queue projection: stage lanes, sort_order rank, lane quick-create, task deep-links. */
export function QueueBoardPage(): React.ReactElement {
  const { queueId = "" } = useParams({ strict: false }) as { queueId?: string };
  const t = useWorkT();
  const queue = useQueueContext(queueId);
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const [creating, setCreating] = React.useState(false);
  const form = useTaskFormDeclaration();
  const name = queue.data?.work_queues_by_pk?.name ?? queueId;
  const scale = queue.data?.work_queues_by_pk?.estimate_scale;
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
    <Page>
      <PageHeader
        title={t("board.title", { queue: name })}
        description={t("board.description")}
      />
      <PageBody gutter="none" scroll="hidden">
        {queue.error ? <ErrorBanner description={queue.error.message} /> : null}
        <ResourceList<WorkTaskRow>
          resource={TASK_MODEL}
          placement="drawer"
          creating={creating}
          onSelect={select}
          onClose={() => setCreating(false)}
          createDefaults={{ queue: queueId }}
        >
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
            rowHref={(row) => routeHref("projects.tasks.record", { id: row.id })}
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
            <Column field="priority" />
            <Column field="due_date" />
          </List>
          {form}
        </ResourceList>
      </PageBody>
    </Page>
  );
}

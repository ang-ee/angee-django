import {
  Column,
  ErrorBanner,
  List,
  useBreadcrumbLeafLabel,
  useRouteParam,
  useRouteHref,
} from "@angee/ui";
import * as React from "react";

import { useQueueContext } from "../context";
import { useCycleRowActions, type WorkCycleRow } from "../cycle-actions";
import { useWorkT } from "../i18n";
import { QueueRecordPage } from "../queue-page";
import { QueueSettingsLink } from "../queue-settings-link";
import { CYCLE_MODEL } from "../resources";

/** Incomplete cycle windows: current first, then upcoming, with close/rollover. */
export function CyclesPage(): React.ReactElement {
  const queueId = useRouteParam("queueId") ?? "";
  const t = useWorkT();
  const routeHref = useRouteHref();
  const queue = useQueueContext(queueId);
  const name = queue.data?.work_queues_by_pk?.name;
  const actions = useCycleRowActions<WorkCycleRow>();
  // The Cycles hub's record page: the queue names the crumb after the hub.
  useBreadcrumbLeafLabel(name);

  return (
    <QueueRecordPage
      eyebrow={t("queue.eyebrow.cycles")}
      title={name ?? t("queue.loading")}
      description={t("cycle.description")}
      verbs={<QueueSettingsLink queueId={queueId} />}
    >
      {queue.error ? <ErrorBanner description={queue.error.message} /> : null}
      <List<WorkCycleRow>
        resource={CYCLE_MODEL}
        scope="local"
        baseFilter={{
          queue: { exact: queueId },
          completed_at: { isNull: true },
        }}
        order={{ starts_on: "ASC" }}
        rowActions={actions.rowActions}
        rowHref={(row) =>
          routeHref("work.cycle-board", { queueId, id: row.id })
        }
        emptyContent={{ title: t("cycle.empty"), description: t("cycle.description") }}
      >
        <Column field="name" header={t("common.name")} />
        <Column field="number" header={t("common.number")} />
        <Column field="starts_on" />
        <Column field="ends_on" />
        <Column field="completed_at" header={t("common.completed")} />
      </List>
      {actions.dialog}
    </QueueRecordPage>
  );
}

import {
  Column,
  ErrorBanner,
  List,
  Page,
  PageBody,
  PageHeader,
  useRouteHref,
} from "@angee/ui";
import { useParams } from "@tanstack/react-router";
import * as React from "react";

import { useQueueContext } from "../context";
import { useCycleRowActions, type WorkCycleRow } from "../cycle-actions";
import { useWorkT } from "../i18n";
import { CYCLE_MODEL } from "../resources";

/** Incomplete cycle windows: current first, then upcoming, with close/rollover. */
export function CyclesPage(): React.ReactElement {
  const { queueId = "" } = useParams({ strict: false }) as { queueId?: string };
  const t = useWorkT();
  const routeHref = useRouteHref();
  const queue = useQueueContext(queueId);
  const name = queue.data?.work_queues_by_pk?.name ?? queueId;
  const actions = useCycleRowActions<WorkCycleRow>();

  return (
    <Page>
      <PageHeader
        title={t("cycle.title", { queue: name })}
        description={t("cycle.description")}
      />
      <PageBody>
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
          emptyContent={t("cycle.empty")}
        >
          <Column field="name" header={t("common.name")} />
          <Column field="number" header={t("common.number")} />
          <Column field="starts_on" />
          <Column field="ends_on" />
          <Column field="completed_at" header={t("common.completed")} />
        </List>
        {actions.dialog}
      </PageBody>
    </Page>
  );
}

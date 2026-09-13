import {
  Column,
  ErrorBanner,
  List,
  Page,
  PageBody,
  PageHeader,
  useRouteParam,
  useRouteHref,
} from "@angee/ui";
import { Outlet, useChildMatches } from "@tanstack/react-router";
import * as React from "react";

import { useQueueContext } from "../context";
import { useCycleRowActions, type WorkCycleRow } from "../cycle-actions";
import { useWorkT } from "../i18n";
import { CYCLE_MODEL } from "../resources";

/** Incomplete cycle windows: current first, then upcoming, with close/rollover. */
export function CyclesPage(): React.ReactElement {
  const queueId = useRouteParam("queueId") ?? "";
  const t = useWorkT();
  const routeHref = useRouteHref();
  const queue = useQueueContext(queueId);
  const name = queue.data?.work_queues_by_pk?.name ?? queueId;
  const actions = useCycleRowActions<WorkCycleRow>();
  // The cycle board is a record route under this one, and it carries its own
  // component rather than being rendered by this page the way a `ResourceList`
  // renders a routed record. A record route must have a parent, so the parent
  // has to stand aside for it: without this the router matched the board and
  // rendered this list instead, and opening a cycle looked like it did nothing.
  const childMatches = useChildMatches();

  if (childMatches.length > 0) return <Outlet />;

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

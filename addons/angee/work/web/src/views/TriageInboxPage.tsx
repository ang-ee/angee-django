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
import { useWorkT } from "../i18n";
import { TriageDwell, type WorkTaskRow } from "../task-work";
import { useTriageRowActions } from "../triage-actions";

const TASK_MODEL = "projects.Task";

/** Queue triage inbox, driven by lifecycle facts and the provisioned triage stage. */
export function TriageInboxPage(): React.ReactElement {
  const { queueId = "" } = useParams({ strict: false }) as { queueId?: string };
  const t = useWorkT();
  const routeHref = useRouteHref();
  const queue = useQueueContext(queueId);
  const name = queue.data?.work_queues_by_pk?.name ?? queueId;
  const triageStageId = queue.data?.work_stages[0]?.id ?? "__missing_triage_stage__";
  const actions = useTriageRowActions<WorkTaskRow>(queueId);

  return (
    <Page>
      <PageHeader
        title={t("triage.title", { queue: name })}
        description={t("triage.description")}
      />
      <PageBody>
        {queue.error ? <ErrorBanner description={queue.error.message} /> : null}
        <List<WorkTaskRow>
          resource={TASK_MODEL}
          scope="local"
          fields={["queue", "started_triage_at", "triaged_at"]}
          baseFilter={{
            queue: { exact: queueId },
            stage: { exact: triageStageId },
            started_triage_at: { isNull: false },
            triaged_at: { isNull: true },
            snoozed_until: { isNull: true },
          }}
          order={{ started_triage_at: "ASC" }}
          rowActions={actions.rowActions}
          rowHref={(row) => routeHref("projects.tasks.record", { id: row.id })}
          emptyContent={t("triage.empty")}
        >
          <Column field="work_key" header={t("common.key")} />
          <Column field="title" />
          <Column field="priority" />
          <Column
            field="started_triage_at"
            header={t("triage.dwell")}
            render={(row) => <TriageDwell value={row.started_triage_at} />}
          />
          <Column field="created_at" />
        </List>
        {actions.dialog}
      </PageBody>
    </Page>
  );
}

import {
  Column,
  List,
  ResourceList,
  useRouteHref,
  useRouteRecordId,
  type RecordSmartButtonDescriptor,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { useWorkT } from "../i18n";
import { useQueueFormDeclaration } from "../queue-form";
import { QUEUE_MODEL } from "../resources";

/** Queue collection and routed settings record: one stacked form, its stages one ordered section. */
export function QueuesPage(): React.ReactElement {
  const t = useWorkT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const recordId = useRouteRecordId();
  const form = useQueueFormDeclaration();
  const smartButtons = React.useMemo<readonly RecordSmartButtonDescriptor[]>(
    () =>
      ([
        ["board", "work-board", "work.board", t("queue.open.board")],
        ["triage", "work-triage", "work.triage", t("queue.open.triage")],
        ["cycles", "work-cycle", "work.cycles", t("queue.open.cycles")],
      ] as const).map(([id, icon, route, label]) => ({
        id: `work-queue-${id}`,
        icon,
        count: "↗",
        label,
        disabled: !recordId || recordId === "new",
        onClick: () => {
          if (!recordId || recordId === "new") return;
          void navigate({ to: routeHref(route, { queueId: recordId }) });
        },
      })),
    [navigate, recordId, routeHref, t],
  );

  return (
    <ResourceList
      resource={QUEUE_MODEL}
      placement="inline"
      routed
      recordSmartButtons={smartButtons}
    >
      <List resource={QUEUE_MODEL} order={{ key: "ASC" }}>
        <Column field="key" header={t("common.key")} />
        <Column field="name" header={t("common.name")} />
        <Column field="triage_enabled" />
        <Column field="cycles_enabled" />
        <Column field="estimate_scale" />
        <Column field="updated_at" />
      </List>
      {form}
    </ResourceList>
  );
}

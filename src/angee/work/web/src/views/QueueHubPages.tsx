import {
  Column,
  List,
  Page,
  PageBody,
  PageHeader,
  useRouteHref,
  type ResourceListSnapshot,
  type StringIdRow,
} from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { useWorkT } from "../i18n";
import { QUEUE_MODEL } from "../resources";

interface QueueHubRow extends StringIdRow {
  key?: unknown;
  name?: unknown;
}

interface QueueHubSpec {
  kind: "triage" | "boards" | "cycles";
  route: "work.triage" | "work.board" | "work.cycles";
  filter?: Record<string, unknown>;
  redirectSingle?: boolean;
}

const TRIAGE_HUB: QueueHubSpec = {
  kind: "triage",
  route: "work.triage",
  filter: { triage_enabled: { exact: true } },
  redirectSingle: true,
};

const BOARDS_HUB: QueueHubSpec = {
  kind: "boards",
  route: "work.board",
};

const CYCLES_HUB: QueueHubSpec = {
  kind: "cycles",
  route: "work.cycles",
  filter: { cycles_enabled: { exact: true } },
};

export function TriageHubPage(): React.ReactElement {
  return <QueueHubPage spec={TRIAGE_HUB} />;
}

export function BoardsHubPage(): React.ReactElement {
  return <QueueHubPage spec={BOARDS_HUB} />;
}

export function CyclesHubPage(): React.ReactElement {
  return <QueueHubPage spec={CYCLES_HUB} />;
}

function QueueHubPage({ spec }: { spec: QueueHubSpec }): React.ReactElement {
  const t = useWorkT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const copy =
    spec.kind === "triage"
      ? {
          title: t("hub.triage.title"),
          description: t("hub.triage.description"),
          emptyTitle: t("hub.triage.empty.title"),
          emptyDescription: t("hub.triage.empty.description"),
        }
      : spec.kind === "boards"
        ? {
            title: t("hub.boards.title"),
            description: t("hub.boards.description"),
            emptyTitle: t("hub.boards.empty.title"),
            emptyDescription: t("hub.boards.empty.description"),
          }
        : {
            title: t("hub.cycles.title"),
            description: t("hub.cycles.description"),
            emptyTitle: t("hub.cycles.empty.title"),
            emptyDescription: t("hub.cycles.empty.description"),
          };
  const [snapshot, setSnapshot] =
    React.useState<ResourceListSnapshot<QueueHubRow> | null>(null);
  const rowHref = React.useCallback(
    (row: QueueHubRow) => routeHref(spec.route, { queueId: row.id }),
    [routeHref, spec.route],
  );

  React.useEffect(() => {
    const row = snapshot?.rows[0];
    if (
      !spec.redirectSingle
      || !snapshot
      || snapshot.fetching
      || snapshot.error
      || snapshot.total !== 1
      || !row
    ) {
      return;
    }
    void navigate({ to: rowHref(row), replace: true });
  }, [navigate, rowHref, snapshot, spec.redirectSingle]);

  return (
    <Page>
      <PageHeader
        title={copy.title}
        description={copy.description}
      />
      <PageBody>
        <List<QueueHubRow>
          resource={QUEUE_MODEL}
          scope="local"
          baseFilter={spec.filter}
          order={{ key: "ASC" }}
          rowHref={rowHref}
          onListStateChange={setSnapshot}
          emptyContent={{
            title: copy.emptyTitle,
            description: copy.emptyDescription,
          }}
        >
          <Column field="key" header={t("common.key")} />
          <Column field="name" header={t("common.name")} />
        </List>
      </PageBody>
    </Page>
  );
}

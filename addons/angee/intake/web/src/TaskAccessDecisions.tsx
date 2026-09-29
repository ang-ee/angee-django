import { RowsListView, TextLink, useRouteHref, type StringIdRow } from "@angee/ui";
import type { ReactElement } from "react";

import { useIntakeT } from "./i18n";

interface CurrentAccessRow extends StringIdRow {
  claimed_name: string;
  access_decision: { id: string; verdict: string; is_open: boolean } | null;
}

/** A prop-driven pane of the Need owner's current decision projection. */
export function TaskAccessDecisions({ needs }: { needs: readonly CurrentAccessRow[] }): ReactElement {
  const t = useIntakeT();
  const routeHref = useRouteHref();
  return <RowsListView<CurrentAccessRow> presentation="embedded" rows={needs}
    columns={[
      { field: "claimed_name", header: t("access.requester") },
      { field: "access_decision.verdict", header: t("access.decision"), render: (row) =>
        row.access_decision ? <TextLink href={routeHref("decisions.decisions.record", { id: row.access_decision.id })}>
          {row.access_decision.verdict}
        </TextLink> : "—" },
    ]}
    emptyContent={t("access.empty")}
  />;
}

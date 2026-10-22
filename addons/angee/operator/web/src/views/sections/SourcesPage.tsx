import { StatusBadge, useRouteHref, type ListColumn } from "@angee/ui";
import { ResourceQuery } from "@angee/metadata";
import { useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import type { SourceState } from "../../data/types";
import { formatDrift } from "../../lib/drift";
import { daemonRowsByName, type DaemonRow } from "../parts/daemon-rows";
import { OperatorRowsList } from "../parts/operator-rows";

type SourceRowData = DaemonRow<SourceState>;
const sourceRowsQuery = ResourceQuery.forRows({ fields: {
  name: { scalar: "String" }, kind: { scalar: "String" },
  status: { scalar: "String", identityPath: "state" },
  branch: { scalar: "String" }, dirty: { scalar: "Boolean" },
} });

/** Sources page: cached git/local sources with a drift readout. Rows open the source record page. */
export function SourcesPage(): ReactNode {
  const t = useOperatorT();
  const routeHref = useRouteHref();

  const columns = useMemo<readonly ListColumn<SourceRowData>[]>(
    () => [
      { field: "name", header: t("sources.column.name") },
      { field: "kind", header: t("sources.column.kind") },
      { field: "status", header: t("sources.column.status"), render: (source) => <StatusBadge value={source.state ?? "unknown"} /> },
      { field: "branch", header: t("sources.column.branch") },
      {
        field: "aheadBehind", header: t("sources.column.aheadBehind"), align: "right",
        render: (source) => <span className="tabular-nums">{formatDrift(source.ahead, source.behind)}</span>,
      },
      { field: "dirty", header: t("sources.column.dirty"), render: (source) => <StatusBadge value={source.dirty ? "dirty" : "clean"} /> },
    ],
    [t],
  );

  return (
    <OperatorRowsList<SourceRowData>
      query={sourceRowsQuery}
      sections={{ sources: true }}
      selectRows={(snapshot) => daemonRowsByName(snapshot.sources)}
      columns={columns}
      rowHref={(source) => routeHref("operator.sources.detail", { name: source.name })}
      emptyContent={t("sources.empty")}
    />
  );
}

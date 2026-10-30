import { MetricStrip, RowsListView, StatusBadge, type ResourceToolbarGroupOption, type ListColumn, type MetricTileValue } from "@angee/ui";
import { ResourceQuery } from "@angee/metadata";
import { useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import type { GitOpsLink, GitOpsSummary } from "../../data/types";
import { formatDrift } from "../../lib/drift";
import type { DaemonRow } from "../parts/daemon-rows";
import { useOperatorRows } from "../parts/operator-rows";

interface SummaryTile {
  id: keyof GitOpsSummary;
  labelKey: string;
}

// A read-only subset of the numeric summary fields, rendered as stat tiles.
const SUMMARY_TILES: readonly SummaryTile[] = [
  { id: "clean", labelKey: "gitops.summary.clean" },
  { id: "dirty", labelKey: "gitops.summary.dirty" },
  { id: "ahead", labelKey: "gitops.summary.ahead" },
  { id: "behind", labelKey: "gitops.summary.behind" },
  { id: "diverged", labelKey: "gitops.summary.diverged" },
  { id: "unpushed", labelKey: "gitops.summary.unpushed" },
];

type GitOpsRow = DaemonRow<GitOpsLink>;
const gitOpsRowsQuery = ResourceQuery.forRows({ fields: {
  source: { scalar: "String" }, workspace: { scalar: "String" }, slot: { scalar: "String" },
  state: { scalar: "String" }, branch: { scalar: "String" }, pushed: { scalar: "Boolean" },
} });

/** GitOps page: a read-only topology summary above the per-link drift list. */
export function GitOpsPage(): ReactNode {
  const t = useOperatorT();
  const { snapshot, rows, fetching, error } = useOperatorRows(
    { gitOps: true },
    (snapshot) => snapshot.gitOps?.links ?? [],
  );
  const gitOps = snapshot?.gitOps ?? null;

  const columns = useMemo<readonly ListColumn<GitOpsRow>[]>(
    () => [
      { field: "source", header: t("gitops.column.source") },
      { field: "workspace", header: t("gitops.column.workspace") },
      { field: "slot", header: t("gitops.column.slot") },
      { field: "state", header: t("gitops.column.status"), render: (link) => <StatusBadge value={link.state} /> },
      { field: "branch", header: t("gitops.column.branch") },
      {
        field: "aheadBehind", header: t("gitops.column.aheadBehind"), align: "right", sortable: false,
        render: (link) => <span className="tabular-nums">{formatDrift(link.ahead, link.behind)}</span>,
      },
      { field: "pushed", header: t("gitops.column.pushed"), render: (link) => <StatusBadge value={link.pushed ? "pushed" : "unpushed"} /> },
    ],
    [t],
  );

  const groupOptions = useMemo<readonly ResourceToolbarGroupOption[]>(
    () => [
      { id: "state", label: t("gitops.column.status"), group: { field: "state" } },
      { id: "slot", label: t("gitops.column.slot"), group: { field: "slot" } },
    ],
    [t],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      {gitOps ? <GitOpsSummary summary={gitOps.summary} /> : null}
      <RowsListView<GitOpsRow>
        query={gitOpsRowsQuery}
        rows={rows}
        columns={columns}
        groupOptions={groupOptions}
        fetching={fetching}
        error={error}
        emptyContent={t("gitops.links.empty")}
      />
    </div>
  );
}

/** The topology summary: a compact metric strip of the daemon's drift counts. */
function GitOpsSummary({ summary }: { summary: GitOpsSummary }): ReactNode {
  const t = useOperatorT();
  const metrics: readonly MetricTileValue[] = SUMMARY_TILES.map((tile) => ({
    label: t(tile.labelKey),
    value: summary[tile.id],
  }));
  return <MetricStrip density="prominent" className="grid-cols-2 sm:grid-cols-3 lg:grid-cols-6" metrics={metrics} />;
}

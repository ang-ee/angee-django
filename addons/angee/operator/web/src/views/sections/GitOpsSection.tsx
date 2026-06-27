import {
  MetricGrid,
  RowsListView,
  textRoleVariants,
  type ResourceToolbarGroupOption,
  type ListColumn,
  type MetricGridTile,
} from "@angee/ui";
import { useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import type { GitOpsLink, GitOpsSummary } from "../../data/types";
import type { DaemonRow } from "../parts/daemon-rows";
import { useOperatorRows } from "../parts/operator-rows";
import { StateTag } from "../parts/StateTag";

interface SummaryTile {
  id: keyof GitOpsSummary;
  labelKey: string;
}

// A read-only subset of the numeric summary fields, rendered as stat tiles.
const SUMMARY_TILES: readonly SummaryTile[] = [
  { id: "clean", labelKey: "operator.gitops.summary.clean" },
  { id: "dirty", labelKey: "operator.gitops.summary.dirty" },
  { id: "ahead", labelKey: "operator.gitops.summary.ahead" },
  { id: "behind", labelKey: "operator.gitops.summary.behind" },
  { id: "diverged", labelKey: "operator.gitops.summary.diverged" },
  { id: "unpushed", labelKey: "operator.gitops.summary.unpushed" },
];

type GitOpsRow = DaemonRow<GitOpsLink>;

/** GitOps pane: a read-only topology summary above the per-link drift list. */
export function GitOpsSection(): ReactNode {
  const t = useOperatorT();
  const { snapshot, rows, fetching, error } = useOperatorRows(
    { gitOps: true },
    (snapshot) => snapshot.gitOps?.links ?? [],
  );
  const gitOps = snapshot?.gitOps ?? null;

  const columns = useMemo<readonly ListColumn<GitOpsRow>[]>(
    () => [
      {
        field: "source",
        header: t("operator.gitops.column.source"),
        render: (link) => <span className="font-medium text-fg">{link.source}</span>,
      },
      {
        field: "workspace",
        header: t("operator.gitops.column.workspace"),
        render: (link) => <span className={textRoleVariants({ role: "meta" })}>{link.workspace}</span>,
      },
      {
        field: "slot",
        header: t("operator.gitops.column.slot"),
        render: (link) => <span className={textRoleVariants({ role: "meta" })}>{link.slot}</span>,
      },
      {
        field: "state",
        header: t("operator.gitops.column.status"),
        render: (link) => <StateTag state={link.state} />,
      },
      {
        field: "branch",
        header: t("operator.gitops.column.branch"),
        render: (link) => <span className={textRoleVariants({ role: "meta" })}>{link.branch ?? "—"}</span>,
      },
      {
        field: "aheadBehind",
        header: t("operator.gitops.column.aheadBehind"),
        align: "right",
        sortable: false,
        render: (link) => (
          <span className="text-13 tabular-nums text-fg-muted">
            ↑{link.ahead ?? 0} ↓{link.behind ?? 0}
          </span>
        ),
      },
      {
        field: "pushed",
        header: t("operator.gitops.column.pushed"),
        render: (link) => (
          <span className={textRoleVariants({ role: "meta" })}>
            {link.pushed ? t("operator.gitops.pushed.yes") : t("operator.gitops.pushed.no")}
          </span>
        ),
      },
    ],
    [t],
  );

  const groupOptions = useMemo<readonly ResourceToolbarGroupOption[]>(
    () => [
      { id: "state", label: t("operator.gitops.column.status"), group: { field: "state" } },
      { id: "slot", label: t("operator.gitops.column.slot"), group: { field: "slot" } },
    ],
    [t],
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      {gitOps ? <GitOpsSummary summary={gitOps.summary} /> : null}
      <RowsListView<GitOpsRow>
        rows={rows}
        columns={columns}
        groupOptions={groupOptions}
        fetching={fetching}
        error={error}
        emptyMessage={t("operator.gitops.links.empty")}
      />
    </div>
  );
}

/** The topology summary: a compact metric strip of the daemon's drift counts. */
function GitOpsSummary({ summary }: { summary: GitOpsSummary }): ReactNode {
  const t = useOperatorT();
  const metrics: readonly MetricGridTile[] = SUMMARY_TILES.map((tile) => ({
    label: t(tile.labelKey),
    value: summary[tile.id],
  }));

  return (
    <MetricGrid
      className="grid-cols-2 sm:grid-cols-3 lg:grid-cols-6"
      metrics={metrics}
    />
  );
}

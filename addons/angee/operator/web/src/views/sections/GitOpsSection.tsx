import {
  EmptyState,
  MetricGrid,
  type MetricGridTile,
} from "@angee/base";
import type { ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import type { GitOpsLink, GitOpsSummary } from "../../data/types";
import { DaemonResourceTable } from "../parts/DaemonResourceTable";
import { OperatorSection } from "../parts/OperatorSection";
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

/** GitOps pane: a read-only summary + per-link drift table from the daemon topology. */
export function GitOpsSection(): ReactNode {
  const t = useOperatorT();
  const { snapshot, result } = useOperatorSnapshot({ gitOps: true });
  const gitOps = snapshot?.gitOps ?? null;

  return (
    <OperatorSection
      title={t("section.operator.gitops.title")}
      loading={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      loadingMessage={t("operator.gitops.loading")}
    >
      {gitOps ? (
        <GitOpsTopologyView summary={gitOps.summary} links={gitOps.links} />
      ) : (
        <EmptyState icon="activity" title={t("operator.gitops.empty.title")} />
      )}
    </OperatorSection>
  );
}

function GitOpsTopologyView({
  summary,
  links,
}: {
  summary: GitOpsSummary;
  links: readonly GitOpsLink[];
}): ReactNode {
  const t = useOperatorT();
  const metrics: readonly MetricGridTile[] = SUMMARY_TILES.map((tile) => ({
    label: t(tile.labelKey),
    value: summary[tile.id],
  }));

  return (
    <>
      <MetricGrid
        className="grid-cols-2 sm:grid-cols-3 lg:grid-cols-6"
        metrics={metrics}
      />

      <DaemonResourceTable
        columns={[
          {
            header: t("operator.gitops.column.source"),
            cell: (link) => <span className="font-medium text-fg">{link.source}</span>,
          },
          {
            header: t("operator.gitops.column.workspace"),
            cell: (link) => <span className="text-13 text-fg-muted">{link.workspace}</span>,
          },
          {
            header: t("operator.gitops.column.slot"),
            cell: (link) => <span className="text-13 text-fg-muted">{link.slot}</span>,
          },
          { header: t("operator.gitops.column.status"), cell: (link) => <StateTag state={link.state} /> },
          {
            header: t("operator.gitops.column.branch"),
            cell: (link) => <span className="text-13 text-fg-muted">{link.branch ?? "—"}</span>,
          },
          {
            header: t("operator.gitops.column.aheadBehind"),
            align: "end",
            cell: (link) => (
              <span className="text-13 tabular-nums text-fg-muted">
                ↑{link.ahead ?? 0} ↓{link.behind ?? 0}
              </span>
            ),
          },
          {
            header: t("operator.gitops.column.pushed"),
            cell: (link) => (
              <span className="text-13 text-fg-muted">
                {link.pushed ? t("operator.gitops.pushed.yes") : t("operator.gitops.pushed.no")}
              </span>
            ),
          },
        ]}
        emptyMessage={t("operator.gitops.links.empty")}
        rowKey={(link) => link.id}
        rows={links}
      />
    </>
  );
}

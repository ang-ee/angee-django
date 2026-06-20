import { RowsListView, type ListColumn } from "@angee/base";
import { useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import type { SourceState } from "../../data/types";
import { sourceDetailPath } from "../../lib/paths";
import { daemonRowsByName, type DaemonRow } from "../parts/daemon-rows";
import { StateTag } from "../parts/StateTag";

type SourceRowData = DaemonRow<SourceState>;

/** Sources pane: cached git/local sources with a drift readout. Rows open the source detail page. */
export function SourcesSection(): ReactNode {
  const t = useOperatorT();
  const { snapshot, result } = useOperatorSnapshot({ sources: true });
  const rows = daemonRowsByName(snapshot?.sources ?? []);

  const columns = useMemo<readonly ListColumn<SourceRowData>[]>(
    () => [
      {
        field: "name",
        header: t("operator.sources.column.name"),
        render: (source) => <span className="font-medium text-fg">{source.name}</span>,
      },
      {
        field: "kind",
        header: t("operator.sources.column.kind"),
        render: (source) => <span className="text-13 text-fg-muted">{source.kind}</span>,
      },
      {
        field: "status",
        header: t("operator.sources.column.status"),
        render: (source) => <StateTag state={source.state ?? "unknown"} />,
      },
      {
        field: "branch",
        header: t("operator.sources.column.branch"),
        render: (source) => <span className="text-13 text-fg-muted">{source.branch ?? "—"}</span>,
      },
      {
        field: "aheadBehind",
        header: t("operator.sources.column.aheadBehind"),
        align: "right",
        render: (source) => (
          <span className="text-13 tabular-nums text-fg-muted">
            ↑{source.ahead ?? 0} ↓{source.behind ?? 0}
          </span>
        ),
      },
      {
        field: "dirty",
        header: t("operator.sources.column.dirty"),
        render: (source) => (
          <span className="text-13 text-fg-muted">
            {source.dirty ? t("operator.sources.dirty") : t("operator.sources.clean")}
          </span>
        ),
      },
    ],
    [t],
  );

  return (
    <RowsListView<SourceRowData>
      rows={rows}
      columns={columns}
      rowHref={(source) => sourceDetailPath(source.name)}
      fetching={result.fetching}
      error={snapshot ? null : result.error}
      emptyMessage={t("operator.sources.empty")}
    />
  );
}

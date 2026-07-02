import { textRoleVariants, type ListColumn } from "@angee/ui";
import { useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import type { SourceState } from "../../data/types";
import { sourceDetailPath } from "../../lib/paths";
import { daemonRowsByName, type DaemonRow } from "../parts/daemon-rows";
import { OperatorRowsList } from "../parts/operator-rows";
import { StateTag } from "../parts/StateTag";

type SourceRowData = DaemonRow<SourceState>;

/** Sources page: cached git/local sources with a drift readout. Rows open the source detail page. */
export function SourcesPage(): ReactNode {
  const t = useOperatorT();

  const columns = useMemo<readonly ListColumn<SourceRowData>[]>(
    () => [
      {
        field: "name",
        header: t("sources.column.name"),
        render: (source) => <span className="font-medium text-fg">{source.name}</span>,
      },
      {
        field: "kind",
        header: t("sources.column.kind"),
        render: (source) => <span className={textRoleVariants({ role: "meta" })}>{source.kind}</span>,
      },
      {
        field: "status",
        header: t("sources.column.status"),
        render: (source) => <StateTag state={source.state ?? "unknown"} />,
      },
      {
        field: "branch",
        header: t("sources.column.branch"),
        render: (source) => <span className={textRoleVariants({ role: "meta" })}>{source.branch ?? "—"}</span>,
      },
      {
        field: "aheadBehind",
        header: t("sources.column.aheadBehind"),
        align: "right",
        render: (source) => (
          <span className={textRoleVariants({ role: "meta", numeric: true })}>
            ↑{source.ahead ?? 0} ↓{source.behind ?? 0}
          </span>
        ),
      },
      {
        field: "dirty",
        header: t("sources.column.dirty"),
        render: (source) => (
          <span className={textRoleVariants({ role: "meta" })}>
            {source.dirty ? t("sources.dirty") : t("sources.clean")}
          </span>
        ),
      },
    ],
    [t],
  );

  return (
    <OperatorRowsList<SourceRowData>
      sections={{ sources: true }}
      selectRows={(snapshot) => daemonRowsByName(snapshot.sources)}
      columns={columns}
      rowHref={(source) => sourceDetailPath(source.name)}
      emptyContent={t("sources.empty")}
    />
  );
}

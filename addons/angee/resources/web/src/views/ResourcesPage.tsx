import { useMemo, type ReactElement } from "react";

import { Code, RowsListView, type ListColumn } from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import { RESOURCE_LEDGER_QUERY, type ResourceLedgerResult } from "../documents";
import { resourceRows, type ResourceRow } from "../lib/rows";

const columns: readonly ListColumn<ResourceRow>[] = [
  {
    field: "source",
    header: "Source",
    render: (row) => (
      <span className="flex min-w-0 flex-col">
        <span className="font-medium text-fg">{row.source}</span>
        <span className="truncate text-2xs text-fg-muted">{row.path}</span>
      </span>
    ),
  },
  { field: "tier", header: "Tier" },
  {
    field: "target",
    header: "Target",
    render: (row) => (
      <span className="flex min-w-0 flex-col">
        <Code truncate>{row.target}</Code>
        {row.targetId ? (
          <span className="truncate text-2xs text-fg-muted">{row.targetId}</span>
        ) : null}
      </span>
    ),
  },
  {
    field: "hash",
    header: "Hash",
    sortable: false,
    render: (row) => <Code truncate tone="muted">{row.hash}</Code>,
  },
  { field: "loaded", header: "Loaded" },
];

export function ResourcesPage(): ReactElement {
  const query = useAuthoredQuery<ResourceLedgerResult>(RESOURCE_LEDGER_QUERY);
  const rows = useMemo(
    () => resourceRows(query.data?.resourceLedger ?? []),
    [query.data],
  );

  return (
    <RowsListView
      rows={rows}
      columns={columns}
      fetching={query.fetching}
      error={query.error}
      defaultGroup={{ field: "tier" }}
      pageSize={100}
      emptyMessage="No imported resources yet."
    />
  );
}

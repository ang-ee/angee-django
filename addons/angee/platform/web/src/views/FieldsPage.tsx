import { useMemo, type ReactElement } from "react";

import { Code, RowsListView, type ListColumn } from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import { PLATFORM_EXPLORER_QUERY, type PlatformExplorerResult } from "../documents";
import { fieldRows, type FieldRow } from "../lib/rows";

const columns: readonly ListColumn<FieldRow>[] = [
  {
    field: "field",
    header: "Field",
    render: (row) => <span className="font-medium text-fg">{row.field}</span>,
  },
  {
    field: "model",
    header: "Model",
    render: (row) => <Code truncate>{row.model}</Code>,
  },
  { field: "addon", header: "Addon" },
  { field: "kind", header: "Type" },
  {
    field: "relationTarget",
    header: "Relation target",
    render: (row) =>
      row.relationTarget ? <Code truncate>{row.relationTarget}</Code> : null,
  },
];

export function FieldsPage(): ReactElement {
  const query = useAuthoredQuery<PlatformExplorerResult>(PLATFORM_EXPLORER_QUERY);
  const rows = useMemo(
    () => fieldRows(query.data?.platformExplorer?.models ?? []),
    [query.data],
  );

  return (
    <RowsListView
      rows={rows}
      columns={columns}
      fetching={query.fetching}
      error={query.error}
      defaultGroup={{ field: "model" }}
      pageSize={100}
      emptyMessage="No fields."
    />
  );
}

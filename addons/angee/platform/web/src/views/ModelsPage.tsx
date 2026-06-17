import { useMemo, type ReactElement } from "react";

import { Code, RowsListView, type ListColumn } from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import { PLATFORM_EXPLORER_QUERY, type PlatformExplorerResult } from "../documents";
import { modelRows, type ModelRow } from "../lib/rows";

const columns: readonly ListColumn<ModelRow>[] = [
  {
    field: "model",
    header: "Model",
    render: (row) => <span className="font-medium text-fg">{row.model}</span>,
  },
  { field: "addon", header: "Addon" },
  {
    field: "table",
    header: "Table",
    render: (row) => <Code truncate>{row.table}</Code>,
  },
  { field: "fields", header: "Fields" },
  { field: "relations", header: "Relations" },
  {
    field: "resourceType",
    header: "Resource type",
    render: (row) =>
      row.resourceType ? <Code truncate>{row.resourceType}</Code> : null,
  },
];

export function ModelsPage(): ReactElement {
  const query = useAuthoredQuery<PlatformExplorerResult>(PLATFORM_EXPLORER_QUERY);
  const rows = useMemo(
    () => modelRows(query.data?.platformExplorer?.models ?? []),
    [query.data],
  );

  return (
    <RowsListView
      rows={rows}
      columns={columns}
      fetching={query.fetching}
      error={query.error}
      defaultGroup={{ field: "addon" }}
      pageSize={50}
      emptyMessage="No models."
    />
  );
}

import { useMemo, type ReactElement } from "react";

import { Badge, Code, RowsListView, type ListColumn } from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import { PLATFORM_EXPLORER_QUERY, type PlatformExplorerResult } from "../documents";
import { addonRows, type AddonRow } from "../lib/rows";

const columns: readonly ListColumn<AddonRow>[] = [
  {
    field: "addon",
    header: "Addon",
    render: (row) => (
      <span className="flex min-w-0 flex-col">
        <span className="font-medium text-fg">{row.addon}</span>
        <span className="truncate text-2xs text-fg-muted">{row.fullName}</span>
      </span>
    ),
  },
  {
    field: "namespace",
    header: "Namespace",
    render: (row) => <Code truncate>{row.namespace}</Code>,
  },
  {
    field: "kind",
    header: "Kind",
    render: (row) => (
      <Badge tone={row.kind === "required" ? "info" : "neutral"}>{row.kind}</Badge>
    ),
  },
  { field: "models", header: "Models" },
  { field: "fields", header: "Fields" },
  { field: "resources", header: "Resources" },
  { field: "dependsOn", header: "Depends on", sortable: false },
];

export function AddonsPage(): ReactElement {
  const query = useAuthoredQuery<PlatformExplorerResult>(PLATFORM_EXPLORER_QUERY);
  const rows = useMemo(
    () => addonRows(query.data?.platformExplorer?.addons ?? []),
    [query.data],
  );

  return (
    <RowsListView
      rows={rows}
      columns={columns}
      fetching={query.fetching}
      error={query.error}
      defaultGroup={{ field: "namespace" }}
      pageSize={50}
      emptyMessage="No addons."
    />
  );
}

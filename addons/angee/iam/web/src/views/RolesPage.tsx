import { useMemo, type ReactElement } from "react";

import {
  Code,
  RowsListView,
  type ListColumn,
} from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import {
  IAM_ROLES_QUERY,
  type IAMRolesData,
} from "../documents";
import {
  roleRows,
  type IAMRoleRow,
} from "../identity-rows";

const roleColumns: readonly ListColumn<IAMRoleRow>[] = [
  {
    field: "namespace",
    render: (row) => <Code truncate>{row.namespace}</Code>,
  },
  {
    field: "label",
    render: (row) => (
      <span className="font-medium text-fg">{row.label}</span>
    ),
  },
  { field: "description", sortable: false },
];

export function RolesPage(): ReactElement {
  const query = useAuthoredQuery<IAMRolesData>(IAM_ROLES_QUERY);
  const rows = useMemo(() => roleRows(query.data?.roles ?? []), [query.data]);

  return (
    <RowsListView
      rows={rows}
      columns={roleColumns}
      fetching={query.fetching}
      error={query.error}
      defaultGroup={{ field: "namespace" }}
      pageSize={50}
    />
  );
}

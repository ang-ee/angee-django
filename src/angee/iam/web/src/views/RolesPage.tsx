import { useMemo, type ReactElement } from "react";

import {
  Code,
  RowsListView,
  type DataToolbarGroupOption,
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
    header: "Namespace",
    render: (row) => <Code truncate>{row.namespace}</Code>,
  },
  {
    field: "label",
    header: "Label",
    render: (row) => (
      <span className="font-medium text-fg">{row.label}</span>
    ),
  },
  { field: "description", header: "Description", sortable: false },
];

const roleGroupOptions: readonly DataToolbarGroupOption[] = [
  {
    id: "namespace",
    label: "Namespace",
    group: { field: "namespace" },
    type: "value",
  },
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
      groupOptions={roleGroupOptions}
      defaultGroup={{ field: "namespace" }}
      pageSize={50}
    />
  );
}

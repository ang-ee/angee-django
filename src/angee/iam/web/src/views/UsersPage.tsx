import { useMemo, type ReactElement } from "react";

import {
  Badge,
  RowsListView,
  type ListColumn,
} from "@angee/base";
import { useAuthoredQuery } from "@angee/sdk";

import {
  IAM_USERS_QUERY,
  type IAMUser,
  type IAMUsersData,
  type IAMUsersVariables,
} from "../documents";

const USER_LIMIT = 500;

const userColumns: readonly ListColumn<IAMUser>[] = [
  {
    field: "username",
    header: "Username",
    render: (row) => (
      <span className="font-medium text-fg">{row.username}</span>
    ),
  },
  { field: "email", header: "Email" },
  {
    field: "firstName",
    header: "Name",
    render: (row) => {
      const name = [row.firstName, row.lastName].filter(Boolean).join(" ");
      return name || <span className="text-fg-muted">-</span>;
    },
  },
  {
    field: "isStaff",
    header: "Staff",
    render: (row) => (
      <Badge variant={row.isStaff ? "info" : "default"}>
        {row.isStaff ? "Staff" : "Member"}
      </Badge>
    ),
  },
  {
    field: "isActive",
    header: "Active",
    render: (row) => (
      <Badge variant={row.isActive ? "success" : "danger"}>
        {row.isActive ? "Active" : "Inactive"}
      </Badge>
    ),
  },
];

export function UsersPage(): ReactElement {
  const variables = useMemo<IAMUsersVariables>(
    () => ({ pagination: { offset: 0, limit: USER_LIMIT } }),
    [],
  );
  const query = useAuthoredQuery<IAMUsersData, IAMUsersVariables>(
    IAM_USERS_QUERY,
    variables,
  );
  const rows = useMemo(
    () => [...(query.data?.users.results ?? [])],
    [query.data],
  );

  return (
    <RowsListView
      rows={rows}
      columns={userColumns}
      fetching={query.fetching}
      error={query.error}
      pageSize={50}
    />
  );
}

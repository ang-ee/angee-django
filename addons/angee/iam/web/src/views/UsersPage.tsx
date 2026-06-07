import type { ReactElement } from "react";

import {
  RowsListView,
  type ListColumn,
} from "@angee/base";
import {
  useResourceList,
  type Row,
} from "@angee/sdk";

const USER_LIMIT = 500;
const USER_FIELDS = [
  "username",
  "email",
  "fullName",
  "isStaff",
  "isActive",
] as const;

interface UserRow extends Row {
  id: string;
  username: string;
  email: string;
  fullName: string;
  isStaff: boolean;
  isActive: boolean;
}

const userColumns: readonly ListColumn<UserRow>[] = [
  { field: "username", header: "Username" },
  { field: "email", header: "Email" },
  { field: "fullName", header: "Name" },
  {
    field: "isStaff",
    header: "Staff",
    widget: "booleanBadge",
    options: [
      { value: "true", label: "Staff" },
      { value: "false", label: "Member" },
    ],
  },
  {
    field: "isActive",
    header: "Active",
    widget: "booleanBadge",
    options: [
      { value: "true", label: "Active" },
      { value: "false", label: "Inactive" },
    ],
  },
];

export function UsersPage(): ReactElement {
  const users = useResourceList("User", {
    fields: USER_FIELDS,
    pageSize: USER_LIMIT,
  });

  return (
    <RowsListView
      rows={users.rows as unknown as readonly UserRow[]}
      columns={userColumns}
      fetching={users.fetching}
      error={users.error}
      pageSize={50}
    />
  );
}

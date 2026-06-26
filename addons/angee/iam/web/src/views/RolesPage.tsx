import { type ReactElement } from "react";

import {
  Code,
  ListView,
  type ListColumn,
} from "@angee/ui";

// The `iam.Role` Hasura resource row (`hasura_pydantic_resource`,
// `addons/angee/iam/schema.py`): roles deduped from active role-relationship
// tuples, fetched + grouped client-side by ListView's client row model.
interface RoleResourceRow extends Record<string, unknown> {
  id: string;
  namespace: string;
  label: string;
  description: string;
}

const roleColumns: readonly ListColumn<RoleResourceRow>[] = [
  {
    field: "namespace",
    render: (row) => <Code truncate>{row.namespace}</Code>,
  },
  {
    field: "label",
    render: (row) => <span className="font-medium text-fg">{row.label}</span>,
  },
  { field: "description", sortable: false },
];

export function RolesPage(): ReactElement {
  return (
    <ListView<RoleResourceRow>
      resource="iam.Role"
      columns={roleColumns}
      defaultGroup={{ field: "namespace" }}
      pageSize={50}
    />
  );
}

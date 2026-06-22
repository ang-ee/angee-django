import { type ReactElement } from "react";

import {
  AuthoredRowsList,
  Code,
  type ListColumn,
} from "@angee/base";
import type { DocumentData } from "@angee/sdk";

import { IamRoles } from "../documents";
import {
  roleRows,
  type IAMRoleRow,
} from "../identity-rows";

type IamRolesResult = DocumentData<typeof IamRoles>;

function selectRows(data: IamRolesResult | undefined): readonly IAMRoleRow[] {
  return roleRows(data?.roles ?? []);
}

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
  return (
    <AuthoredRowsList
      document={IamRoles}
      selectRows={selectRows}
      columns={roleColumns}
      defaultGroup={{ field: "namespace" }}
      pageSize={50}
    />
  );
}

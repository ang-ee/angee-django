import type { ReactElement } from "react";
import { ListView } from "@angee/ui";

import type { IAMGrant } from "../documents";
import { useRoleGrantActions, useRoleGrantColumns } from "../role-grants";

export function GrantsPage(): ReactElement {
  const columns = useRoleGrantColumns();
  const rowActions = useRoleGrantActions();
  return <ListView<IAMGrant>
    resource="iam.Grant"
    columns={columns}
    rowActions={rowActions}
    defaultGroup={{ field: "namespace" }}
    pageSize={50}
  />;
}

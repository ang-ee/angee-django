import { useMemo, type ReactElement } from "react";
import { Code, ListView, type ListColumn } from "@angee/ui";

import type { IAMRole } from "../documents";
import { useIamT } from "../i18n";

export function RolesPage(): ReactElement {
  const t = useIamT();
  const columns = useMemo<readonly ListColumn<IAMRole>[]>(() => [
    { field: "namespace", render: (row) => <Code truncate>{row.namespace}</Code> },
    { field: "label" },
    {
      field: "declared",
      header: t("roles.declaration"),
      render: (row) => row.grantable
        ? t("roles.declared")
        : row.declared ? t("roles.derived") : t("roles.legacy"),
    },
  ], [t]);
  return <ListView<IAMRole>
    resource="iam.Role"
    columns={columns}
    defaultGroup={{ field: "namespace" }}
    pageSize={50}
  />;
}

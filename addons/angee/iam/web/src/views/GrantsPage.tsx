import { useMemo, type ReactElement } from "react";

import {
  Code,
  ListView,
  defineRowAction,
  type ListColumn,
  type RowActionDeclaration,
} from "@angee/ui";

import { IAM_ROLE_MUTATION_INVALIDATES, IamRevokeRole } from "../documents";
import { useIamT } from "../i18n";

// The `iam.Grant` Hasura resource row (`hasura_pydantic_resource`,
// `addons/angee/iam/schema.py`): direct user role-grant tuples, fetched +
// grouped client-side by ListView's client row model. The revoke stays an
// authored single-row mutation (`revoke_role(principal_id, role)`) rendered as a
// per-row action column.
interface GrantResourceRow extends Record<string, unknown> {
  id: string;
  principal_id: string;
  principal_ref: string;
  principal_label: string;
  role: string;
  role_name: string;
  namespace: string;
}

export function GrantsPage(): ReactElement {
  const t = useIamT();

  const grantColumns = useMemo<readonly ListColumn<GrantResourceRow>[]>(
    () => [
      {
        field: "principal_label",
        header: t("grants.column.principal"),
        render: (row) => (
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-13 text-fg">{row.principal_label}</span>
            <Code truncate tone="muted" className="text-2xs">
              {row.principal_ref}
            </Code>
          </span>
        ),
      },
      {
        field: "role",
        header: t("grants.column.role"),
        render: (row) => (
          <div className="min-w-0">
            <div className="truncate font-medium text-fg">{row.role_name}</div>
            <Code truncate tone="muted">
              {row.role}
            </Code>
          </div>
        ),
      },
      {
        field: "namespace",
        header: t("grants.column.namespace"),
        render: (row) => <Code truncate>{row.namespace}</Code>,
      },
    ],
    [t],
  );
  const rowActions = useMemo<readonly RowActionDeclaration<GrantResourceRow>[]>(
    () => [
      defineRowAction({
        kind: "authored",
        id: "revoke-role",
        label: t("revoke"),
        document: IamRevokeRole,
        variables: (row: GrantResourceRow) => ({
          principal_id: row.principal_id,
          role: row.role,
        }),
        succeeded: (result) => result?.revoke_role === true,
        invalidateModels: IAM_ROLE_MUTATION_INVALIDATES,
        confirm: {
          title: () => t("grants.revoke.title"),
          body: (row) => t("grants.revoke.body", {
            role: row.role,
            principal: row.principal_label,
          }),
          confirm: () => t("revoke"),
          cancel: () => t("grants.revoke.cancel"),
        },
        toast: {
          title: () => t("grants.revoke.failedTitle"),
          description: () => t("grants.revoke.error"),
        },
        variant: "danger",
        pendingPolicy: "active-row",
      }),
    ],
    [t],
  );

  return (
    <ListView<GrantResourceRow>
      resource="iam.Grant"
      columns={grantColumns}
      rowActions={rowActions}
      defaultGroup={{ field: "namespace" }}
      pageSize={50}
    />
  );
}

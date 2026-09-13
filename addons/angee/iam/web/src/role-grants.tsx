import { useMemo } from "react";
import { Code, defineRowAction, type ListColumn, type RowActionDeclaration } from "@angee/ui";

import { IAM_ROLE_MUTATION_INVALIDATES, IamRevokeRole, type IAMGrant } from "./documents";
import { useIamT } from "./i18n";

/** Shared presentation and exact-tuple revoke for the hub and overview. */
export function useRoleGrantColumns(): readonly ListColumn<IAMGrant>[] {
  const t = useIamT();
  return useMemo(() => [
    {
      field: "subject_label",
      header: t("grants.column.subject"),
      render: (row: IAMGrant) => <span className="flex min-w-0 flex-col">
        <span className="truncate text-13 text-fg">{row.subject_label}</span>
        <Code truncate tone="muted" className="text-2xs">{row.subject}</Code>
      </span>,
    },
    {
      field: "role",
      header: t("grants.column.role"),
      render: (row: IAMGrant) => <span className="flex min-w-0 flex-col">
        <span className="truncate font-medium text-fg">{row.role_name}</span>
        <Code truncate tone="muted">{row.role}</Code>
        {row.caveat_name ? <Code truncate tone="muted">{row.caveat_name}</Code> : null}
      </span>,
    },
    { field: "namespace", header: t("grants.column.namespace") },
  ], [t]);
}

export function useRoleGrantActions(): readonly RowActionDeclaration<IAMGrant>[] {
  const t = useIamT();
  return useMemo(() => [defineRowAction({
    kind: "authored",
    id: "revoke-role",
    label: t("revoke"),
    document: IamRevokeRole,
    variables: (row: IAMGrant) => ({ subject: row.subject, role: row.role, caveat_name: row.caveat_name }),
    succeeded: (result) => result?.revoke_role === true,
    invalidateModels: IAM_ROLE_MUTATION_INVALIDATES,
    confirm: {
      title: () => t("grants.revoke.title"),
      body: (row: IAMGrant) => t("grants.revoke.body", { role: row.role, subject: row.subject_label }),
      confirm: () => t("revoke"),
      cancel: () => t("grants.revoke.cancel"),
    },
    toast: {
      title: () => t("grants.revoke.failedTitle"),
      description: () => t("grants.revoke.error"),
    },
    variant: "danger",
    pendingPolicy: "active-row",
  })], [t]);
}

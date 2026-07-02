import { useAuthoredMutation } from "@angee/refine";
import { useMemo, useState, type ReactElement, } from "react";

import {
  Alert, Button, Code, ListView, errorMessage, useConfirm, type ListColumn } from "@angee/ui";

import { IamRevokeRole } from "../documents";
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

// Revoking a grant removes a REBAC role tuple, which is what both the grants
// list (iam.Grant) and the relationships list (iam.Relationship) render — so
// refresh both. The old "rebac.RelationshipRegistry" label matched no resource
// and threw in resourceInvalidationTargets at render.
const GRANT_INVALIDATES = ["iam.Grant", "iam.Relationship"];

export function GrantsPage(): ReactElement {
  const t = useIamT();
  const confirm = useConfirm();
  const [revoke_role, revokeState] = useAuthoredMutation(IamRevokeRole, {
    invalidateModels: GRANT_INVALIDATES,
    shouldInvalidate: (result) => result?.revoke_role === true,
  });
  const [pendingGrantId, setPendingGrantId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  async function revoke(row: GrantResourceRow): Promise<void> {
    const confirmed = await confirm({
      title: t("grants.revoke.title"),
      body: t("grants.revoke.body", { role: row.role, principal: row.principal_label }),
      cancel: t("grants.revoke.cancel"),
      confirm: t("revoke"),
      danger: true,
    });
    if (!confirmed) return;
    setPendingGrantId(row.id);
    setActionError(null);
    try {
      const result = await revoke_role({
        principal_id: row.principal_id,
        role: row.role,
      });
      if (result?.revoke_role === false) {
        throw new Error(t("grants.revoke.error"));
      }
    } catch (caught) {
      setActionError(errorMessage(caught, t("grants.revoke.error")));
    } finally {
      setPendingGrantId(null);
    }
  }

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
      {
        field: "actions",
        header: "",
        sortable: false,
        align: "right",
        render: (row) => (
          <Button
            type="button"
            size="sm"
            variant="danger"
            pending={pendingGrantId === row.id && revokeState.fetching}
            disabled={pendingGrantId !== null && pendingGrantId !== row.id}
            onClick={() => void revoke(row)}
          >
            {t("revoke")}
          </Button>
        ),
      },
    ],
    [pendingGrantId, revokeState.fetching, t],
  );

  return (
    <div className="flex flex-col gap-3">
      {actionError ? (
        <Alert tone="danger" title={t("grants.revoke.failedTitle")}>
          {actionError}
        </Alert>
      ) : null}
      <ListView<GrantResourceRow>
        resource="iam.Grant"
        columns={grantColumns}
        defaultGroup={{ field: "namespace" }}
        pageSize={50}
      />
    </div>
  );
}

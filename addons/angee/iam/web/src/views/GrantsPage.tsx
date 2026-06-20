import {
  useMemo,
  useState,
  type ReactElement,
} from "react";

import {
  Alert,
  Button,
  Code,
  RowsListView,
  useConfirm,
  type ListColumn,
} from "@angee/base";
import {
  errorMessage,
  useAuthoredMutation,
  useAuthoredQuery,
} from "@angee/sdk";

import {
  IamGrants,
  IamRevokeRole,
  type IAMGrantsVariables,
} from "../documents";
import {
  grantRows,
  type IAMGrantRow,
} from "../identity-rows";
import { IAM_LIST_LIMIT } from "../list-config";
import { useIamT } from "../i18n";

export function GrantsPage(): ReactElement {
  const t = useIamT();
  const confirm = useConfirm();
  const variables = useMemo<IAMGrantsVariables>(
    () => ({ pagination: { offset: 0, limit: IAM_LIST_LIMIT } }),
    [],
  );
  const query = useAuthoredQuery(IamGrants, variables);
  const [revokeRole, revokeState] = useAuthoredMutation(IamRevokeRole);
  const [pendingGrantId, setPendingGrantId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const rows = useMemo(
    () => grantRows(query.data?.grants.results ?? []),
    [query.data],
  );

  async function revoke(row: IAMGrantRow): Promise<void> {
    const confirmed = await confirm({
      title: t("iam.grants.revoke.title"),
      body: t("iam.grants.revoke.body", { role: row.role, principal: row.principalLabel }),
      cancel: t("iam.grants.revoke.cancel"),
      confirm: t("iam.revoke"),
      danger: true,
    });
    if (!confirmed) return;
    setPendingGrantId(row.id);
    setActionError(null);
    try {
      const result = await revokeRole({
        principalId: row.principalId,
        role: row.role,
      });
      if (result?.revokeRole === false) {
        throw new Error(t("iam.grants.revoke.error"));
      }
      query.refetch();
    } catch (caught) {
      setActionError(errorMessage(caught, t("iam.grants.revoke.error")));
    } finally {
      setPendingGrantId(null);
    }
  }

  const grantColumns = useMemo<readonly ListColumn<IAMGrantRow>[]>(
    () => [
      {
        field: "principalLabel",
        header: t("iam.grants.column.principal"),
        render: (row) => (
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-13 text-fg">{row.principalLabel}</span>
            <Code truncate tone="muted" className="text-2xs">
              {row.principalRef}
            </Code>
          </span>
        ),
      },
      {
        field: "role",
        header: t("iam.grants.column.role"),
        render: (row) => (
          <div className="min-w-0">
            <div className="truncate font-medium text-fg">{row.roleName}</div>
            <Code truncate tone="muted">
              {row.role}
            </Code>
          </div>
        ),
      },
      {
        field: "namespace",
        header: t("iam.grants.column.namespace"),
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
            {t("iam.revoke")}
          </Button>
        ),
      },
    ],
    [pendingGrantId, revokeState.fetching, t],
  );

  return (
    <div className="flex flex-col gap-3">
      {actionError ? (
        <Alert tone="danger" title={t("iam.grants.revoke.failedTitle")}>
          {actionError}
        </Alert>
      ) : null}
      <RowsListView
        rows={rows}
        columns={grantColumns}
        fetching={query.fetching}
        error={query.error}
        defaultGroup={{ field: "namespace" }}
        pageSize={50}
      />
    </div>
  );
}

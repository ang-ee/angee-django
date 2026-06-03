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
  type DataToolbarGroupOption,
  type ListColumn,
} from "@angee/base";
import {
  useAuthoredMutation,
  useAuthoredQuery,
} from "@angee/sdk";

import {
  IAM_GRANTS_QUERY,
  IAM_REVOKE_ROLE_MUTATION,
  type IAMGrantsData,
  type IAMGrantsVariables,
  type IAMRevokeRoleData,
  type IAMRevokeRoleVariables,
} from "../documents";
import {
  grantRows,
  type IAMGrantRow,
} from "../identity-rows";

const GRANT_LIMIT = 500;

const grantGroupOptions: readonly DataToolbarGroupOption[] = [
  {
    id: "namespace",
    label: "Namespace",
    group: { field: "namespace" },
    type: "value",
  },
];

export function GrantsPage(): ReactElement {
  const confirm = useConfirm();
  const variables = useMemo<IAMGrantsVariables>(
    () => ({ pagination: { offset: 0, limit: GRANT_LIMIT } }),
    [],
  );
  const query = useAuthoredQuery<IAMGrantsData, IAMGrantsVariables>(
    IAM_GRANTS_QUERY,
    variables,
  );
  const [revokeRole, revokeState] = useAuthoredMutation<
    IAMRevokeRoleData,
    IAMRevokeRoleVariables
  >(IAM_REVOKE_ROLE_MUTATION);
  const [pendingGrantId, setPendingGrantId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const rows = useMemo(
    () => grantRows(query.data?.grants.results ?? []),
    [query.data],
  );

  async function revoke(row: IAMGrantRow): Promise<void> {
    const confirmed = await confirm({
      title: "Revoke role?",
      body: `Revoke ${row.role} from ${row.principalLabel}?`,
      cancel: "Keep role",
      confirm: "Revoke",
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
        throw new Error("Could not revoke role.");
      }
      query.refetch();
    } catch (caught) {
      setActionError(
        caught instanceof Error ? caught.message : "Could not revoke role.",
      );
    } finally {
      setPendingGrantId(null);
    }
  }

  const grantColumns = useMemo<readonly ListColumn<IAMGrantRow>[]>(
    () => [
      {
        field: "principalLabel",
        header: "Principal",
        render: (row) => (
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-13 text-fg">{row.principalLabel}</span>
            <Code truncate variant="muted" className="text-2xs">
              {row.principalRef}
            </Code>
          </span>
        ),
      },
      {
        field: "role",
        header: "Role",
        render: (row) => (
          <div className="min-w-0">
            <div className="truncate font-medium text-fg">{row.roleName}</div>
            <Code truncate variant="muted">
              {row.role}
            </Code>
          </div>
        ),
      },
      {
        field: "namespace",
        header: "Namespace",
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
            Revoke
          </Button>
        ),
      },
    ],
    [pendingGrantId, revokeState.fetching],
  );

  return (
    <div className="flex flex-col gap-3">
      {actionError ? (
        <Alert intent="danger" title="Role was not revoked">
          {actionError}
        </Alert>
      ) : null}
      <RowsListView
        rows={rows}
        columns={grantColumns}
        fetching={query.fetching}
        error={query.error}
        groupOptions={grantGroupOptions}
        defaultGroup={{ field: "namespace" }}
        pageSize={50}
      />
    </div>
  );
}

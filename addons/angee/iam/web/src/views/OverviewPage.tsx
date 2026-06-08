import {
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactElement,
} from "react";

import {
  Alert,
  Button,
  DashboardView,
  InlineEmpty,
  Metric,
  MiniCard,
  Select,
  SurfacePanel,
} from "@angee/base";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/sdk";

import {
  IAM_GRANTS_QUERY,
  IAM_GRANT_ROLE_MUTATION,
  IAM_OVERVIEW_QUERY,
  IAM_REVOKE_ROLE_MUTATION,
  IAM_USERS_QUERY,
  type IAMGrantRoleData,
  type IAMGrantRoleVariables,
  type IAMGrantsData,
  type IAMGrantsVariables,
  type IAMOverviewData,
  type IAMOverviewVariables,
  type IAMRevokeRoleData,
  type IAMRevokeRoleVariables,
  type IAMUsersData,
  type IAMUsersVariables,
} from "../documents";
import { titleLabel, userLabel } from "../identity-labels";
import { grantRows, roleRef, roleRows, type IAMGrantRow } from "../identity-rows";
import { IAM_LIST_LIMIT } from "../list-config";

const OVERVIEW_COUNT_LIMIT = 1;
const PEEK_LIMIT = 6;

/**
 * The identity overview — an aggregate dashboard. A metric band over the
 * permission inventory, the role-grant composer, and three peek panels
 * (privileged grants, role namespaces, unassigned principals) derived from the
 * same reads. Writes route through the grant/revoke mutations and refetch.
 */
export function OverviewPage(): ReactElement {
  const countVars = useMemo<IAMOverviewVariables>(
    () => ({ pagination: { offset: 0, limit: OVERVIEW_COUNT_LIMIT } }),
    [],
  );
  const listVars = useMemo<IAMUsersVariables & IAMGrantsVariables>(
    () => ({ pagination: { offset: 0, limit: IAM_LIST_LIMIT } }),
    [],
  );
  const overview = useAuthoredQuery<IAMOverviewData, IAMOverviewVariables>(
    IAM_OVERVIEW_QUERY,
    countVars,
  );
  const usersQuery = useAuthoredQuery<IAMUsersData, IAMUsersVariables>(
    IAM_USERS_QUERY,
    listVars,
  );
  const grantsQuery = useAuthoredQuery<IAMGrantsData, IAMGrantsVariables>(
    IAM_GRANTS_QUERY,
    listVars,
  );
  const [grantRole, grantState] = useAuthoredMutation<
    IAMGrantRoleData,
    IAMGrantRoleVariables
  >(IAM_GRANT_ROLE_MUTATION);

  const roles = useMemo(() => roleRows(overview.data?.roles ?? []), [overview.data]);
  const users = useMemo(
    () => [...(usersQuery.data?.users.results ?? [])],
    [usersQuery.data],
  );
  const grants = useMemo(
    () => grantRows(grantsQuery.data?.grants.results ?? []),
    [grantsQuery.data],
  );

  const roleOptions = useMemo(
    () =>
      roles.map((role) => ({
        value: roleRef(role),
        label: `${role.namespace} / ${role.label}`,
      })),
    [roles],
  );
  const principalOptions = useMemo(
    () => users.map((user) => ({ value: user.id, label: userLabel(user) })),
    [users],
  );
  const userTotalCount = usersQuery.data?.users.totalCount ?? 0;
  const usersTruncated = userTotalCount > IAM_LIST_LIMIT;

  // Namespaces with their role + grant counts, sorted by namespace.
  const namespaces = useMemo(() => {
    const byNamespace = new Map<string, { roles: number; grants: number }>();
    for (const role of roles) {
      const entry = byNamespace.get(role.namespace) ?? { roles: 0, grants: 0 };
      entry.roles += 1;
      byNamespace.set(role.namespace, entry);
    }
    for (const grant of grants) {
      const entry = byNamespace.get(grant.namespace) ?? { roles: 0, grants: 0 };
      entry.grants += 1;
      byNamespace.set(grant.namespace, entry);
    }
    return [...byNamespace.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [roles, grants]);

  const privileged = useMemo(() => grants.filter(isPrivileged), [grants]);
  const assignedIds = useMemo(
    () => new Set(grants.map((grant) => grant.principalId)),
    [grants],
  );
  const unassigned = useMemo(
    () => users.filter((user) => !assignedIds.has(user.id)),
    [users, assignedIds],
  );

  const [principalId, setPrincipalId] = useState("");
  const [role, setRole] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!roleOptions.some((option) => option.value === role)) {
      setRole(roleOptions[0]?.value ?? "");
    }
  }, [roleOptions, role]);
  useEffect(() => {
    if (principalId && !principalOptions.some((o) => o.value === principalId)) {
      setPrincipalId("");
    }
  }, [principalOptions, principalId]);

  async function handleGrant(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!principalId || !role) {
      setError("Choose a principal and role before granting access.");
      return;
    }
    setError(null);
    try {
      const result = await grantRole({ principalId, role });
      if (result?.grantRole === false) throw new Error("Could not grant role.");
      setPrincipalId("");
      grantsQuery.refetch();
      overview.refetch();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not grant role.");
    }
  }

  const loading = overview.fetching || grantsQuery.fetching;

  return (
    <DashboardView className="p-1">
      <Metric label="Users" value={count(overview.data?.users.totalCount, loading)} icon="users" />
      <Metric label="Roles" value={count(roles.length, loading)} icon="auth" variant="brand" />
      <Metric label="Grants" value={count(overview.data?.grants.totalCount, loading)} icon="check" variant="success" />
      <Metric label="Relationships" value={count(overview.data?.relationships.totalCount, loading)} icon="share" variant="info" />
      <Metric label="Privileged" value={count(privileged.length, loading)} icon="auth" variant="warning" detail="admin-tier grants" />
      <Metric label="Unassigned" value={count(unassigned.length, loading)} icon="users" variant="danger" detail="no direct roles" />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="space-y-6">
          <SurfacePanel title="Grant access" summary="Direct role binding for a user or group.">
            <div className="p-4">
              <form
                className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(12rem,16rem)_auto]"
                onSubmit={(event) => void handleGrant(event)}
              >
                <label className="grid min-w-0 gap-1.5 text-13 font-medium text-fg">
                  Principal
                  <Select
                    value={principalId}
                    options={principalOptions}
                    placeholder={usersQuery.fetching ? "Loading users" : "Select user"}
                    aria-label="Principal"
                    disabled={usersQuery.fetching || principalOptions.length === 0}
                    onValueChange={setPrincipalId}
                  />
                  {usersTruncated ? (
                    <span className="text-12 font-normal text-fg-muted">
                      Showing first {IAM_LIST_LIMIT.toLocaleString()} of{" "}
                      {userTotalCount.toLocaleString()} users.
                    </span>
                  ) : null}
                </label>
                <label className="grid min-w-0 gap-1.5 text-13 font-medium text-fg">
                  Role
                  <Select
                    value={role}
                    options={roleOptions}
                    placeholder="Select role"
                    aria-label="Role"
                    onValueChange={setRole}
                  />
                </label>
                <div className="flex items-end">
                  <Button type="submit" variant="primary" pending={grantState.fetching} disabled={!principalId || !role}>
                    Grant
                  </Button>
                </div>
              </form>
              {error ? (
                <Alert className="mt-3" intent="danger" title="Role was not granted">{error}</Alert>
              ) : null}
            </div>
          </SurfacePanel>

          <SurfacePanel
            title="Privileged grants"
            summary={`${privileged.length.toLocaleString()} admin-tier grants`}
          >
            <div className="divide-y divide-border-subtle">
              {privileged.slice(0, PEEK_LIMIT).map((grant) => (
                <PrivilegedGrantRow key={grant.id} grant={grant} onRevoked={() => {
                  grantsQuery.refetch();
                  overview.refetch();
                }} />
              ))}
              {privileged.length === 0 ? (
                <div className="p-4"><InlineEmpty label="No admin-tier grants." /></div>
              ) : null}
            </div>
          </SurfacePanel>
        </div>

        <div className="space-y-6">
          <SurfacePanel
            title="Role namespaces"
            summary={`${namespaces.length.toLocaleString()} namespaces`}
          >
            <div className="space-y-3 p-4">
              {namespaces.map(([namespace, counts]) => (
                <MiniCard
                  key={namespace}
                  title={titleLabel(namespace)}
                  meta={`${counts.roles.toLocaleString()} ${counts.roles === 1 ? "role" : "roles"}`}
                  primaryTag={{
                    label: `${counts.grants.toLocaleString()} grants`,
                    variant: counts.grants > 0 ? "brand" : "default",
                  }}
                />
              ))}
              {namespaces.length === 0 ? <InlineEmpty label="No roles defined." /> : null}
            </div>
          </SurfacePanel>

          <SurfacePanel
            title="Unassigned principals"
            summary={`${unassigned.length.toLocaleString()} without direct roles`}
          >
            <div className="divide-y divide-border-subtle">
              {unassigned.slice(0, PEEK_LIMIT).map((user) => (
                <div key={user.id} className="flex items-center justify-between gap-3 px-4 py-3">
                  <div className="min-w-0">
                    <div className="truncate text-13 font-medium text-fg">{user.username}</div>
                    <div className="truncate text-2xs text-fg-muted">{user.email}</div>
                  </div>
                </div>
              ))}
              {unassigned.length === 0 ? (
                <div className="p-4"><InlineEmpty label="Every principal has a role." /></div>
              ) : null}
            </div>
          </SurfacePanel>
        </div>
      </div>
    </DashboardView>
  );
}

function PrivilegedGrantRow({
  grant,
  onRevoked,
}: {
  grant: IAMGrantRow;
  onRevoked: () => void;
}): ReactElement {
  const [revoke, state] = useAuthoredMutation<
    IAMRevokeRoleData,
    IAMRevokeRoleVariables
  >(IAM_REVOKE_ROLE_MUTATION);
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <div className="truncate text-13 font-medium text-fg">{grant.principalLabel}</div>
        <div className="truncate text-2xs text-fg-muted">{titleLabel(grant.namespace)} · {grant.roleName}</div>
      </div>
      <Button
        variant="danger"
        size="sm"
        pending={state.fetching}
        onClick={() => {
          void revoke({ principalId: grant.principalId, role: grant.role }).then(onRevoked);
        }}
      >
        Revoke
      </Button>
    </div>
  );
}

/** Admin-tier heuristic: any grant whose role id reads as an admin role. */
function isPrivileged(grant: IAMGrantRow): boolean {
  return grant.roleName.toLowerCase().includes("admin");
}

function count(value: number | undefined, loading: boolean): string {
  if (value === undefined && loading) return "—";
  return (value ?? 0).toLocaleString();
}

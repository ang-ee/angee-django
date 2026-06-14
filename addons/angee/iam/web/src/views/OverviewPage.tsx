import {
  useEffect,
  useId,
  useMemo,
  useState,
  type FormEvent,
  type ReactElement,
} from "react";

import {
  Alert,
  Button,
  DashboardView,
  FieldDescription,
  FieldLabel,
  FieldRoot,
  InlineEmpty,
  Metric,
  MiniCard,
  Select,
  SurfacePanel,
} from "@angee/base";
import { errorMessage, useAuthoredMutation, useAuthoredQuery } from "@angee/sdk";

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
import { useIamT } from "../i18n";

const OVERVIEW_COUNT_LIMIT = 1;
const PEEK_LIMIT = 6;

/**
 * The identity overview — an aggregate dashboard. A metric band over the
 * permission inventory, the role-grant composer, and three peek panels
 * (privileged grants, role namespaces, unassigned principals) derived from the
 * same reads. Writes route through the grant/revoke mutations and refetch.
 */
export function OverviewPage(): ReactElement {
  const t = useIamT();
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
  const principalLabelId = useId();
  const roleLabelId = useId();

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
      setError(t("iam.overview.grant.chooseBoth"));
      return;
    }
    setError(null);
    try {
      const result = await grantRole({ principalId, role });
      if (result?.grantRole === false) throw new Error(t("iam.overview.grant.error"));
      setPrincipalId("");
      grantsQuery.refetch();
      overview.refetch();
    } catch (caught) {
      setError(errorMessage(caught, t("iam.overview.grant.error")));
    }
  }

  const loading = overview.fetching || grantsQuery.fetching;

  return (
    <DashboardView className="p-1">
      <Metric label={t("iam.overview.metric.users")} value={count(overview.data?.users.totalCount, loading)} icon="users" />
      <Metric label={t("iam.overview.metric.roles")} value={count(roles.length, loading)} icon="auth" tone="brand" />
      <Metric label={t("iam.overview.metric.grants")} value={count(overview.data?.grants.totalCount, loading)} icon="check" tone="success" />
      <Metric label={t("iam.overview.metric.relationships")} value={count(overview.data?.relationships.totalCount, loading)} icon="share" tone="info" />
      <Metric label={t("iam.overview.metric.privileged")} value={count(privileged.length, loading)} icon="auth" tone="warning" detail={t("iam.overview.metric.privilegedDetail")} />
      <Metric label={t("iam.overview.metric.unassigned")} value={count(unassigned.length, loading)} icon="users" tone="danger" detail={t("iam.overview.metric.unassignedDetail")} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="space-y-6">
          <SurfacePanel title={t("iam.overview.grant.title")} summary={t("iam.overview.grant.summary")}>
            <div className="p-4">
              <form
                className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(12rem,16rem)_auto]"
                onSubmit={(event) => void handleGrant(event)}
              >
                <FieldRoot>
                  {/* A Select trigger is a button, not a labelable control, so the
                      label renders as a span and associates via aria-labelledby. */}
                  <FieldLabel id={principalLabelId} nativeLabel={false} render={<span />}>
                    {t("iam.overview.grant.principal")}
                  </FieldLabel>
                  <Select
                    value={principalId}
                    options={principalOptions}
                    placeholder={usersQuery.fetching ? t("iam.overview.grant.loadingUsers") : t("iam.overview.grant.selectUser")}
                    aria-labelledby={principalLabelId}
                    disabled={usersQuery.fetching || principalOptions.length === 0}
                    onValueChange={setPrincipalId}
                  />
                  {usersTruncated ? (
                    <FieldDescription>
                      {t("iam.overview.grant.truncated", {
                        shown: IAM_LIST_LIMIT.toLocaleString(),
                        total: userTotalCount.toLocaleString(),
                      })}
                    </FieldDescription>
                  ) : null}
                </FieldRoot>
                <FieldRoot>
                  <FieldLabel id={roleLabelId} nativeLabel={false} render={<span />}>
                    {t("iam.overview.grant.role")}
                  </FieldLabel>
                  <Select
                    value={role}
                    options={roleOptions}
                    placeholder={t("iam.overview.grant.selectRole")}
                    aria-labelledby={roleLabelId}
                    onValueChange={setRole}
                  />
                </FieldRoot>
                <div className="flex items-end">
                  <Button type="submit" variant="primary" pending={grantState.fetching} disabled={!principalId || !role}>
                    {t("iam.overview.grant.submit")}
                  </Button>
                </div>
              </form>
              {error ? (
                <Alert className="mt-3" tone="danger" title={t("iam.overview.grant.failedTitle")}>{error}</Alert>
              ) : null}
            </div>
          </SurfacePanel>

          <SurfacePanel
            title={t("iam.overview.privileged.title")}
            summary={t("iam.overview.privileged.summary", { count: privileged.length.toLocaleString() })}
          >
            <div className="divide-y divide-border-subtle">
              {privileged.slice(0, PEEK_LIMIT).map((grant) => (
                <PrivilegedGrantRow key={grant.id} grant={grant} onRevoked={() => {
                  grantsQuery.refetch();
                  overview.refetch();
                }} />
              ))}
              {privileged.length === 0 ? (
                <div className="p-4"><InlineEmpty label={t("iam.overview.privileged.empty")} /></div>
              ) : null}
            </div>
          </SurfacePanel>
        </div>

        <div className="space-y-6">
          <SurfacePanel
            title={t("iam.overview.namespaces.title")}
            summary={t("iam.overview.namespaces.summary", { count: namespaces.length.toLocaleString() })}
          >
            <div className="space-y-3 p-4">
              {namespaces.map(([namespace, counts]) => (
                <MiniCard
                  key={namespace}
                  title={titleLabel(namespace)}
                  meta={
                    counts.roles === 1
                      ? t("iam.overview.namespaces.roleCount.one", { count: counts.roles.toLocaleString() })
                      : t("iam.overview.namespaces.roleCount.other", { count: counts.roles.toLocaleString() })
                  }
                  primaryTag={{
                    label: t("iam.overview.namespaces.grantCount", { count: counts.grants.toLocaleString() }),
                    tone: counts.grants > 0 ? "brand" : "neutral",
                  }}
                />
              ))}
              {namespaces.length === 0 ? <InlineEmpty label={t("iam.overview.namespaces.empty")} /> : null}
            </div>
          </SurfacePanel>

          <SurfacePanel
            title={t("iam.overview.unassigned.title")}
            summary={t("iam.overview.unassigned.summary", { count: unassigned.length.toLocaleString() })}
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
                <div className="p-4"><InlineEmpty label={t("iam.overview.unassigned.empty")} /></div>
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
  const t = useIamT();
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
        {t("iam.revoke")}
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

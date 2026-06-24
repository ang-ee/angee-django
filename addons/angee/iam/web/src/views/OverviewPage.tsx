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
  errorMessage,
} from "@angee/base";
import { useAuthoredMutation, useAuthoredQuery } from "@angee/sdk";

import {
  IamGrantRole,
  IamOverview,
  IamRevokeRole,
  IamUsers,
  type IAMOverviewVariables,
  type IAMUsersVariables,
} from "../documents";
import { titleLabel, userLabel } from "../identity-labels";
import { grantRows, roleRef, roleRows, type IAMGrantRow } from "../identity-rows";
import { IAM_LIST_LIMIT } from "../list-config";
import { useIamT } from "../i18n";

const PEEK_LIMIT = 6;

/**
 * The identity overview — an aggregate dashboard. A metric band over the
 * permission inventory, the role-grant composer, and three peek panels
 * (privileged grants, role namespaces, unassigned principals) derived from the
 * same reads. Writes route through the grant/revoke mutations and refetch.
 */
export function OverviewPage(): ReactElement {
  const t = useIamT();
  const overviewVars = useMemo<IAMOverviewVariables>(
    () => ({ peekLimit: PEEK_LIMIT }),
    [],
  );
  const listVars = useMemo<IAMUsersVariables>(
    () => ({ offset: 0, limit: IAM_LIST_LIMIT }),
    [],
  );
  const overview = useAuthoredQuery(IamOverview, overviewVars);
  const usersQuery = useAuthoredQuery(IamUsers, listVars);
  const [grantRole, grantState] = useAuthoredMutation(IamGrantRole);

  const overviewFacts = overview.data?.iamOverview;
  const roles = useMemo(() => roleRows(overview.data?.roles ?? []), [overview.data]);
  const users = useMemo(
    () => [...(usersQuery.data?.users ?? [])],
    [usersQuery.data],
  );
  const privileged = useMemo(
    () => grantRows(overviewFacts?.privilegedGrants ?? []),
    [overviewFacts],
  );
  const namespaces = overviewFacts?.namespaces ?? [];
  const unassigned = useMemo(
    () => [...(overviewFacts?.unassignedUsers ?? [])],
    [overviewFacts],
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
  const userTotalCount = usersQuery.data?.users_aggregate.aggregate?.count ?? 0;
  const usersTruncated = userTotalCount > IAM_LIST_LIMIT;
  const privilegedTotal = overviewFacts?.privilegedGrantCount ?? privileged.length;
  const unassignedTotal = overviewFacts?.unassignedUserCount ?? unassigned.length;

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
      overview.refetch();
    } catch (caught) {
      setError(errorMessage(caught, t("iam.overview.grant.error")));
    }
  }

  const loading = overview.fetching;

  return (
    <DashboardView className="p-1">
      <Metric label={t("iam.overview.metric.users")} value={count(overviewFacts?.userCount, loading)} icon="users" />
      <Metric label={t("iam.overview.metric.roles")} value={count(overviewFacts?.roleCount, loading)} icon="auth" tone="brand" />
      <Metric label={t("iam.overview.metric.grants")} value={count(overviewFacts?.grantCount, loading)} icon="check" tone="success" />
      <Metric label={t("iam.overview.metric.relationships")} value={count(overviewFacts?.relationshipCount, loading)} icon="share" tone="info" />
      <Metric label={t("iam.overview.metric.privileged")} value={count(overviewFacts?.privilegedGrantCount, loading)} icon="auth" tone="warning" detail={t("iam.overview.metric.privilegedDetail")} />
      <Metric label={t("iam.overview.metric.unassigned")} value={count(overviewFacts?.unassignedUserCount, loading)} icon="users" tone="danger" detail={t("iam.overview.metric.unassignedDetail")} />

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
            summary={t("iam.overview.privileged.summary", { count: privilegedTotal.toLocaleString() })}
          >
            <div className="divide-y divide-border-subtle">
              {privileged.map((grant) => (
                <PrivilegedGrantRow key={grant.id} grant={grant} onRevoked={() => {
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
              {namespaces.map((namespace) => (
                <MiniCard
                  key={namespace.namespace}
                  title={titleLabel(namespace.namespace)}
                  meta={
                    namespace.roleCount === 1
                      ? t("iam.overview.namespaces.roleCount.one", { count: namespace.roleCount.toLocaleString() })
                      : t("iam.overview.namespaces.roleCount.other", { count: namespace.roleCount.toLocaleString() })
                  }
                  primaryTag={{
                    label: t("iam.overview.namespaces.grantCount", { count: namespace.grantCount.toLocaleString() }),
                    tone: namespace.grantCount > 0 ? "brand" : "neutral",
                  }}
                />
              ))}
              {namespaces.length === 0 ? <InlineEmpty label={t("iam.overview.namespaces.empty")} /> : null}
            </div>
          </SurfacePanel>

          <SurfacePanel
            title={t("iam.overview.unassigned.title")}
            summary={t("iam.overview.unassigned.summary", { count: unassignedTotal.toLocaleString() })}
          >
            <div className="divide-y divide-border-subtle">
              {unassigned.map((user) => (
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
  const [revoke, state] = useAuthoredMutation(IamRevokeRole);
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

function count(value: number | undefined, loading: boolean): string {
  if (value === undefined && loading) return "—";
  return (value ?? 0).toLocaleString();
}

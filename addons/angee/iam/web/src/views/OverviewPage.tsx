import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import { useMemo, type ReactElement, } from "react";

import {
  Button, DashboardView, InlineEmpty, Metric, MiniCard, MutationDialog, SurfacePanel, mutationDialogValueCodecs, textRoleVariants, titleCase, type MutationDialogField } from "@angee/ui";

import {
  IamGrantRole,
  IamOverview,
  IamRevokeRole,
  IamUsers,
  type IAMGrant,
  type IAMOverviewVariables,
  type IAMUsersVariables,
} from "../documents";
import { userLabel } from "../identity-labels";
import { grantRows } from "../identity-rows";
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
  const [grantRole] = useAuthoredMutation(IamGrantRole);

  const overviewFacts = overview.data?.iam_overview;
  const roles = overview.data?.iam_roles ?? [];
  const users = useMemo(
    () => [...(usersQuery.data?.users ?? [])],
    [usersQuery.data],
  );
  const privileged = useMemo(
    () => grantRows(overviewFacts?.privileged_grants ?? []),
    [overviewFacts],
  );
  const namespaces = overviewFacts?.namespaces ?? [];
  const unassigned = useMemo(
    () => [...(overviewFacts?.unassigned_users ?? [])],
    [overviewFacts],
  );

  const roleOptions = useMemo(
    () =>
      roles.map((role) => ({
        value: role.id,
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
  const privilegedTotal = overviewFacts?.privileged_grant_count ?? privileged.length;
  const unassignedTotal = overviewFacts?.unassigned_user_count ?? unassigned.length;

  const grantFields = useMemo<readonly MutationDialogField[]>(() => [
    {
      name: "principal_id",
      label: t("overview.grant.principal"),
      widget: "select",
      options: principalOptions,
      placeholder: usersQuery.isFetching
        ? t("overview.grant.loadingUsers")
        : t("overview.grant.selectUser"),
      description: usersTruncated
        ? t("overview.grant.truncated", {
          shown: IAM_LIST_LIMIT.toLocaleString(),
          total: userTotalCount.toLocaleString(),
        })
        : undefined,
      required: true,
      readOnly: usersQuery.isFetching || principalOptions.length === 0,
    },
    {
      name: "role",
      label: t("overview.grant.role"),
      widget: "select",
      options: roleOptions,
      placeholder: t("overview.grant.selectRole"),
      required: true,
      readOnly: roleOptions.length === 0,
    },
  ], [principalOptions, roleOptions, t, userTotalCount, usersQuery.isFetching, usersTruncated]);

  const loading = overview.isFetching;

  return (
    <DashboardView className="p-1">
      <Metric label={t("overview.metric.users")} value={overviewFacts?.user_count} format="count" loading={loading} icon="users" />
      <Metric label={t("overview.metric.roles")} value={overviewFacts?.role_count} format="count" loading={loading} icon="auth" tone="brand" />
      <Metric label={t("overview.metric.grants")} value={overviewFacts?.grant_count} format="count" loading={loading} icon="check" tone="success" />
      <Metric label={t("overview.metric.relationships")} value={overviewFacts?.relationship_count} format="count" loading={loading} icon="share" tone="info" />
      <Metric label={t("overview.metric.privileged")} value={overviewFacts?.privileged_grant_count} format="count" loading={loading} icon="auth" tone="warning" detail={t("overview.metric.privilegedDetail")} />
      <Metric label={t("overview.metric.unassigned")} value={overviewFacts?.unassigned_user_count} format="count" loading={loading} icon="users" tone="danger" detail={t("overview.metric.unassignedDetail")} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="space-y-6">
          <SurfacePanel title={t("overview.grant.title")} summary={t("overview.grant.summary")}>
            <div className="p-4">
              <MutationDialog
                trigger={(
                  <Button
                    type="button"
                    variant="primary"
                    disabled={usersQuery.isFetching || principalOptions.length === 0 || roleOptions.length === 0}
                  >
                    {t("overview.grant.submit")}
                  </Button>
                )}
                title={t("overview.grant.title")}
                description={t("overview.grant.summary")}
                fields={grantFields}
                initialValues={{ role: roleOptions[0]?.value ?? "" }}
                submitLabel={t("overview.grant.submit")}
                errorFallback={t("overview.grant.error")}
                parseValues={(values) => ({
                  principal_id: mutationDialogValueCodecs.requiredString(values.principal_id, "principal_id"),
                  role: mutationDialogValueCodecs.requiredString(values.role, "role"),
                })}
                onSubmit={async (values) => {
                  const result = await grantRole(values);
                  if (result?.grant_role === false) throw new Error(t("overview.grant.error"));
                }}
                onSubmitted={() => {
                  void overview.refetch();
                }}
              />
            </div>
          </SurfacePanel>

          <SurfacePanel
            title={t("overview.privileged.title")}
            summary={t("overview.privileged.summary", { count: privilegedTotal.toLocaleString() })}
          >
            <div className="divide-y divide-border-subtle">
              {privileged.map((grant) => (
                <PrivilegedGrantRow key={`${grant.principal_ref}:${grant.role}`} grant={grant} onRevoked={() => {
                  overview.refetch();
                }} />
              ))}
              {privileged.length === 0 ? (
                <div className="p-4"><InlineEmpty label={t("overview.privileged.empty")} /></div>
              ) : null}
            </div>
          </SurfacePanel>
        </div>

        <div className="space-y-6">
          <SurfacePanel
            title={t("overview.namespaces.title")}
            summary={t("overview.namespaces.summary", { count: namespaces.length.toLocaleString() })}
          >
            <div className="space-y-3 p-4">
              {namespaces.map((namespace) => (
                <MiniCard
                  key={namespace.namespace}
                  title={titleCase(namespace.namespace)}
                  meta={t("overview.namespaces.roleCount", {
                    count: namespace.role_count,
                  })}
                  primaryTag={{
                    label: t("overview.namespaces.grantCount", { count: namespace.grant_count.toLocaleString() }),
                    tone: namespace.grant_count > 0 ? "brand" : "neutral",
                  }}
                />
              ))}
              {namespaces.length === 0 ? <InlineEmpty label={t("overview.namespaces.empty")} /> : null}
            </div>
          </SurfacePanel>

          <SurfacePanel
            title={t("overview.unassigned.title")}
            summary={t("overview.unassigned.summary", { count: unassignedTotal.toLocaleString() })}
          >
            <div className="divide-y divide-border-subtle">
              {unassigned.map((user) => (
                <div key={user.id} className="flex items-center justify-between gap-3 px-4 py-3">
                  <div className="min-w-0">
                    <div className="truncate text-13 font-medium text-fg">{user.username}</div>
                    <div className={textRoleVariants({ role: "caption", truncate: true })}>{user.email}</div>
                  </div>
                </div>
              ))}
              {unassigned.length === 0 ? (
                <div className="p-4"><InlineEmpty label={t("overview.unassigned.empty")} /></div>
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
  grant: IAMGrant;
  onRevoked: () => void;
}): ReactElement {
  const t = useIamT();
  const [revoke, state] = useAuthoredMutation(IamRevokeRole);
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3">
      <div className="min-w-0">
        <div className="truncate text-13 font-medium text-fg">{grant.principal_label}</div>
        <div className={textRoleVariants({ role: "caption", truncate: true })}>{titleCase(grant.namespace)} · {grant.role_name}</div>
      </div>
      <Button
        variant="danger"
        size="sm"
        pending={state.fetching}
        onClick={() => {
          void revoke({ principal_id: grant.principal_id, role: grant.role }).then(onRevoked);
        }}
      >
        {t("revoke")}
      </Button>
    </div>
  );
}

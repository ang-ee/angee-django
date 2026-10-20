import { useMemo, type ReactElement } from "react";
import { useAuthoredQuery } from "@angee/refine";

import {
  formSubmitError, Button, DashboardView, InlineEmpty, Metric, MiniCard, MutationDialog, RowsListView, SurfacePanel, formatNumber, mutationDialogValueCodecs, titleCase, useAuthoredResourceMutation, useResourceRecordHref, useRouteHref, type DescriptorField, type ListColumn } from "@angee/ui";

import {
  IamGrantRole,
  IamOverview,
  IAM_ROLE_MUTATION_INVALIDATES,
  type IAMOverviewVariables,
} from "../documents";
import { grantRows } from "../identity-rows";
import { useIamT } from "../i18n";
import { SubjectControl } from "../SubjectControl";
import { useRoleGrantActions, useRoleGrantColumns } from "../role-grants";

const PEEK_LIMIT = 6;

interface UnassignedUserRow extends Record<string, unknown> {
  id: string;
  username: string;
  email: string;
}

/**
 * The identity overview — an aggregate dashboard. A metric band over the
 * permission inventory, then three peek panels (privileged grants with the
 * grant composer as the panel's verb, role namespaces, unassigned principals)
 * derived from the same reads. Writes route through the grant/revoke mutations
 * and refetch.
 */
export function OverviewPage(): ReactElement {
  const t = useIamT();
  const routeHref = useRouteHref();
  const userHref = useResourceRecordHref("iam.User");
  const overviewVars = useMemo<IAMOverviewVariables>(
    () => ({ peekLimit: PEEK_LIMIT }),
    [],
  );
  const overview = useAuthoredQuery(IamOverview, overviewVars, { models: IAM_ROLE_MUTATION_INVALIDATES });
  const [grantRole] = useAuthoredResourceMutation(IamGrantRole, { invalidateModels: IAM_ROLE_MUTATION_INVALIDATES });
  const grantColumns = useRoleGrantColumns();
  const grantActions = useRoleGrantActions();

  const overviewFacts = overview.data?.iam_overview;
  const roles = useMemo(() => overview.data?.iam_roles ?? [], [overview.data]);
  const privileged = useMemo(
    () => grantRows(overviewFacts?.privileged_grants ?? []),
    [overviewFacts],
  );
  const namespaces = overviewFacts?.namespaces ?? [];
  const unassigned = useMemo<readonly UnassignedUserRow[]>(
    () => (overviewFacts?.unassigned_users ?? []).map((user) => ({ id: user.id, username: user.username ?? "", email: user.email ?? "" })),
    [overviewFacts],
  );
  // `sublineColumn` directly: RowsListView does not resolve `subline` for local rows (a ui gap).
  const unassignedColumns = useMemo<readonly ListColumn<UnassignedUserRow>[]>(
    () => [{ field: "username", header: t("overview.unassigned.user"), sublineColumn: { field: "email" } }],
    [t],
  );

  const roleOptions = useMemo(
    () =>
      roles.filter((role) => role.grantable).map((role) => ({
        value: role.id,
        label: `${role.namespace} / ${role.label}`,
      })),
    [roles],
  );
  const privilegedTotal = overviewFacts?.privileged_grant_count ?? privileged.length;
  const unassignedTotal = overviewFacts?.unassigned_user_count ?? unassigned.length;

  const grantFields = useMemo<readonly DescriptorField[]>(() => [
    {
      name: "subject",
      label: t("overview.grant.subject"),
      control: (props) => <SubjectControl {...props} />,
      controlLabelMode: "group",
      required: true,
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
  ], [roleOptions, t]);

  const loading = overview.isFetching;

  return (
    <DashboardView className="p-1">
      <Metric label={t("overview.metric.users")} value={overviewFacts?.user_count} format="count" loading={loading} icon="users" href={routeHref("iam.users")} />
      <Metric label={t("overview.metric.roles")} value={overviewFacts?.role_count} format="count" loading={loading} icon="auth" tone="brand" href={routeHref("iam.roles")} />
      <Metric label={t("overview.metric.grants")} value={overviewFacts?.grant_count} format="count" loading={loading} icon="check" tone="success" href={routeHref("iam.grants")} />
      <Metric label={t("overview.metric.relationships")} value={overviewFacts?.relationship_count} format="count" loading={loading} icon="share" tone="info" href={routeHref("iam.relationships")} />
      <Metric label={t("overview.metric.privileged")} value={overviewFacts?.privileged_grant_count} format="count" loading={loading} icon="auth" tone="warning" detail={t("overview.metric.privilegedDetail")} href={routeHref("iam.grants")} />
      <Metric label={t("overview.metric.unassigned")} value={overviewFacts?.unassigned_user_count} format="count" loading={loading} icon="users" tone="danger" detail={t("overview.metric.unassignedDetail")} href={routeHref("iam.users")} />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="space-y-6">
          <SurfacePanel
            title={t("overview.privileged.title")}
            summary={t("overview.privileged.summary", { count: formatNumber(privilegedTotal) })}
            actions={(
              <MutationDialog
                trigger={(
                  <Button type="button" variant="primary" size="sm" disabled={roleOptions.length === 0}>
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
                  subject: mutationDialogValueCodecs.requiredString(values.subject, "subject"),
                  role: mutationDialogValueCodecs.requiredString(values.role, "role"),
                })}
                onSubmit={async (values) => {
                  const result = await grantRole(values);
                  return result?.grant_role === true
                    ? { status: "ok", data: result }
                    : formSubmitError(t("overview.grant.error"));
                }}
              />
            )}
          >
            <RowsListView
              rows={privileged}
              columns={grantColumns}
              rowActions={grantActions}
              fetching={overview.isFetching}
              error={overview.error}
              selectable={false}
              emptyContent={t("overview.privileged.empty")}
              scope="local"
            />
          </SurfacePanel>
        </div>

        <div className="space-y-6">
          <SurfacePanel
            title={t("overview.namespaces.title")}
            summary={t("overview.namespaces.summary", { count: formatNumber(namespaces.length) })}
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
                    label: t("overview.namespaces.grantCount", { count: formatNumber(namespace.grant_count) }),
                    tone: namespace.grant_count > 0 ? "brand" : "neutral",
                  }}
                />
              ))}
              {namespaces.length === 0 ? <InlineEmpty label={t("overview.namespaces.empty")} /> : null}
            </div>
          </SurfacePanel>

          <SurfacePanel
            title={t("overview.unassigned.title")}
            summary={t("overview.unassigned.summary", { count: formatNumber(unassignedTotal) })}
          >
            <RowsListView
              rows={unassigned}
              columns={unassignedColumns}
              rowHref={userHref ? (user) => userHref(user.id) ?? "" : undefined}
              fetching={overview.isFetching}
              error={overview.error}
              selectable={false}
              scope="local"
              emptyContent={t("overview.unassigned.empty")}
            />
          </SurfacePanel>
        </div>
      </div>
    </DashboardView>
  );
}

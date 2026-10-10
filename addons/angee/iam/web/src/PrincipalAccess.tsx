import { useAuthoredQuery } from "@angee/refine";
import {
  Code,
  InlineEmpty,
  RowsListView,
  Tabs,
  NavLink,
  useRecordChromeContext,
  useResourceRecordHref,
  useResourceRoute,
  type ListColumn,
  type RecordTabDescriptor,
} from "@angee/ui";
import * as React from "react";

import {
  IAM_ROLE_MUTATION_INVALIDATES,
  IamPrincipalAccess,
  type IAMPrincipalGrant,
  type IAMPrincipalPermission,
  type IAMPrincipalRole,
} from "./documents";
import { useIamT } from "./i18n";

type AccessTargetRow = Pick<
  IAMPrincipalGrant | IAMPrincipalPermission,
  "resource" | "target_id" | "target_model"
>;

function AccessTarget({ row }: { row: AccessTargetRow }): React.ReactElement {
  if (!row.target_model) return <Code truncate>{row.resource}</Code>;
  return <RoutedAccessTarget row={row} targetModel={row.target_model} />;
}

function RoutedAccessTarget({
  row,
  targetModel,
}: {
  row: AccessTargetRow;
  targetModel: string;
}): React.ReactElement {
  const recordHref = useResourceRecordHref(targetModel);
  const collectionHref = useResourceRoute(targetModel);
  const href = (row.target_id ? recordHref?.(row.target_id) : undefined) ?? collectionHref;
  return href
    ? <NavLink href={href} variant="inline">{row.resource}</NavLink>
    : <Code truncate>{row.resource}</Code>;
}

/** Return the one IAM-owned Access tab for any record exposing a principal subject. */
export function usePrincipalAccessRecordTab(): RecordTabDescriptor {
  const t = useIamT();
  return React.useMemo(
    () => ({
      id: "access",
      label: t("principalAccess.tab"),
      icon: "auth",
      render: () => <PrincipalAccessTab />,
    }),
    [t],
  );
}

/** Administrative roles, explicit grants, and schema permission paths for a principal. */
export function PrincipalAccessTab(): React.ReactElement {
  const t = useIamT();
  const chrome = useRecordChromeContext();
  const subject = typeof chrome.record?.assignment_subject === "string"
    ? chrome.record.assignment_subject
    : "";
  const query = useAuthoredQuery(
    IamPrincipalAccess,
    subject ? { subject } : undefined,
    { enabled: Boolean(subject), models: IAM_ROLE_MUTATION_INVALIDATES },
  );
  const access = query.data?.iam_principal_access;
  const roles = React.useMemo(() => access?.roles ?? [], [access?.roles]);
  const grants = React.useMemo(() => access?.grants ?? [], [access?.grants]);
  const permissions = React.useMemo(
    () => access?.permissions ?? [],
    [access?.permissions],
  );
  const roleColumns = React.useMemo<readonly ListColumn<IAMPrincipalRole>[]>(() => [
    {
      field: "role_name",
      header: t("principalAccess.role"),
      render: (row) => (
        <div className="min-w-0">
          <div>{row.role_name}</div>
          <Code truncate>{row.role}</Code>
        </div>
      ),
    },
    { field: "namespace", header: t("principalAccess.namespace") },
    {
      field: "source_label",
      header: t("principalAccess.source"),
      render: (row) => (
        <div className="min-w-0">
          <div>{row.source_label}</div>
          {row.source ? <Code truncate>{row.source}</Code> : null}
        </div>
      ),
    },
    {
      field: "direct",
      header: t("principalAccess.scope"),
      render: (row) => row.direct
        ? t("principalAccess.direct")
        : t("principalAccess.inherited"),
    },
  ], [t]);
  const grantColumns = React.useMemo<readonly ListColumn<IAMPrincipalGrant>[]>(() => [
    {
      field: "resource",
      header: t("principalAccess.target"),
      render: (row) => <AccessTarget row={row} />,
    },
    { field: "relation", header: t("principalAccess.relation") },
    {
      field: "source_label",
      header: t("principalAccess.source"),
      render: (row) => (
        <div className="min-w-0">
          <div>{row.source_label}</div>
          <Code truncate>{row.source}</Code>
        </div>
      ),
    },
    {
      field: "direct",
      header: t("principalAccess.scope"),
      render: (row) => row.direct
        ? t("principalAccess.direct")
        : t("principalAccess.inherited"),
    },
    { field: "caveat_name", header: t("principalAccess.condition") },
  ], [t]);
  const permissionColumns = React.useMemo<
    readonly ListColumn<IAMPrincipalPermission>[]
  >(() => [
    {
      field: "resource",
      header: t("principalAccess.target"),
      render: (row) => <AccessTarget row={row} />,
    },
    { field: "permission", header: t("principalAccess.permission") },
    {
      field: "source",
      header: t("principalAccess.source"),
      render: (row) => <Code truncate>{row.source}</Code>,
    },
    {
      field: "direct",
      header: t("principalAccess.scope"),
      render: (row) => row.direct
        ? t("principalAccess.direct")
        : t("principalAccess.inherited"),
    },
    { field: "caveat_name", header: t("principalAccess.condition") },
  ], [t]);

  if (!chrome.record) return <InlineEmpty label={t("principalAccess.loading")} />;
  if (!subject) return <InlineEmpty label={t("principalAccess.missingSubject")} />;

  return (
    <Tabs defaultValue="roles" variant="card">
      <Tabs.List aria-label={t("principalAccess.tab")}>
        <Tabs.Tab value="roles">
          {t("principalAccess.roles")} <Tabs.Count>{roles.length}</Tabs.Count>
        </Tabs.Tab>
        <Tabs.Tab value="grants">
          {t("principalAccess.grants")} <Tabs.Count>{grants.length}</Tabs.Count>
        </Tabs.Tab>
        <Tabs.Tab value="permissions">
          {t("principalAccess.permissions")} <Tabs.Count>{permissions.length}</Tabs.Count>
        </Tabs.Tab>
      </Tabs.List>
      <Tabs.Panel value="roles">
        <RowsListView
          rows={roles}
          columns={roleColumns}
          fetching={query.isFetching}
          error={query.error}
          selectable={false}
          scope="local"
          emptyContent={t("principalAccess.noRoles")}
        />
      </Tabs.Panel>
      <Tabs.Panel value="grants">
        <RowsListView
          rows={grants}
          columns={grantColumns}
          fetching={query.isFetching}
          error={query.error}
          selectable={false}
          scope="local"
          emptyContent={t("principalAccess.noGrants")}
        />
      </Tabs.Panel>
      <Tabs.Panel value="permissions">
        <p className="mb-3 text-sm text-fg-muted">
          {t("principalAccess.permissionsDescription")}
        </p>
        <RowsListView
          rows={permissions}
          columns={permissionColumns}
          fetching={query.isFetching}
          error={query.error}
          selectable={false}
          scope="local"
          emptyContent={t("principalAccess.noPermissions")}
        />
      </Tabs.Panel>
    </Tabs>
  );
}

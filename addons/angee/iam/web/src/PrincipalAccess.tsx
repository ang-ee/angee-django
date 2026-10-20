import { useAuthoredQuery } from "@angee/refine";
import {
  Code,
  InlineEmpty,
  NavLink,
  RowsListView,
  SectionHeading,
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

/**
 * Administrative roles, explicit grants, and schema permission paths for a
 * principal: three titled sections of one tab body (a record tab never nests a
 * second tab strip).
 */
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
  const permissions = React.useMemo(() => access?.permissions ?? [], [access?.permissions]);
  const scope = React.useCallback(
    (row: { direct: boolean }) => (row.direct ? t("principalAccess.direct") : t("principalAccess.inherited")),
    [t],
  );
  // `sublineColumn` directly: RowsListView does not resolve `subline` for local rows (a ui gap).
  const roleColumns = React.useMemo<readonly ListColumn<IAMPrincipalRole>[]>(() => [
    { field: "role_name", header: t("principalAccess.role"), sublineColumn: { field: "role" } },
    { field: "namespace", header: t("principalAccess.namespace") },
    { field: "source_label", header: t("principalAccess.source"), sublineColumn: { field: "source" } },
    { field: "direct", header: t("principalAccess.scope"), render: scope },
  ], [scope, t]);
  const grantColumns = React.useMemo<readonly ListColumn<IAMPrincipalGrant>[]>(() => [
    { field: "resource", header: t("principalAccess.target"), render: (row) => <AccessTarget row={row} /> },
    { field: "relation", header: t("principalAccess.relation") },
    { field: "source_label", header: t("principalAccess.source"), sublineColumn: { field: "source" } },
    { field: "direct", header: t("principalAccess.scope"), render: scope },
    { field: "caveat_name", header: t("principalAccess.condition") },
  ], [scope, t]);
  const permissionColumns = React.useMemo<readonly ListColumn<IAMPrincipalPermission>[]>(() => [
    { field: "resource", header: t("principalAccess.target"), render: (row) => <AccessTarget row={row} /> },
    { field: "permission", header: t("principalAccess.permission") },
    { field: "source", header: t("principalAccess.source"), render: (row) => <Code truncate>{row.source}</Code> },
    { field: "direct", header: t("principalAccess.scope"), render: scope },
    { field: "caveat_name", header: t("principalAccess.condition") },
  ], [scope, t]);

  if (!chrome.record) return <InlineEmpty label={t("principalAccess.loading")} />;
  if (!subject) return <InlineEmpty label={t("principalAccess.missingSubject")} />;

  const list = { fetching: query.isFetching, error: query.error, selectable: false, scope: "local" } as const;
  return (
    <div className="grid gap-6">
      <section className="grid gap-2">
        <SectionHeading label={t("principalAccess.roles")} count={roles.length} />
        <RowsListView rows={roles} columns={roleColumns} {...list} emptyContent={t("principalAccess.noRoles")} />
      </section>
      <section className="grid gap-2">
        <SectionHeading label={t("principalAccess.grants")} count={grants.length} />
        <RowsListView rows={grants} columns={grantColumns} {...list} emptyContent={t("principalAccess.noGrants")} />
      </section>
      <section className="grid gap-2">
        <SectionHeading label={t("principalAccess.permissions")} count={permissions.length} summary={t("principalAccess.permissionsDescription")} />
        <RowsListView rows={permissions} columns={permissionColumns} {...list} emptyContent={t("principalAccess.noPermissions")} />
      </section>
    </div>
  );
}

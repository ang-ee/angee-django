import { type Row } from "@angee/metadata";
import { useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import { Button, Column, EmptyState, ErrorBanner, List, LoadingPanel, ResourceList, errorMessage, useResourceRecordHrefLookup, useRouteHref, useRouteRecordId } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";

import { IntegrationRecordRedirectDocument } from "../documents";
import { useIntegrateT } from "../i18n";

const MODEL = "integrate.Integration";
interface ConcreteTarget { state?: string | null; resource?: string | null; id?: string | null; }
function concreteTarget(row: Row): ConcreteTarget | null {
  const value = row.concrete_target;
  return value && typeof value === "object" ? value as ConcreteTarget : null;
}

export function concreteIntegrationHref(
  row: Row,
  lookup: (resource: string, id: string) => string | undefined,
): string {
  const target = concreteTarget(row);
  if (target?.state !== "AVAILABLE" || !target.resource || !target.id) return "";
  return lookup(target.resource, target.id) ?? "";
}

/** Resolve a generic parent record link through its authorized concrete target. */
export function IntegrationRecordRedirect(): React.ReactElement {
  const t = useIntegrateT();
  const id = useRouteRecordId() ?? "";
  const navigate = useNavigate();
  const recordHref = useResourceRecordHrefLookup();
  const query = useAuthoredQuery(IntegrationRecordRedirectDocument, { id }, {
    models: [MODEL], enabled: Boolean(id),
  });
  const record = query.data?.integrations_by_pk;
  const href = record && !query.error ? concreteIntegrationHref(record, recordHref) : "";

  React.useEffect(() => {
    if (!href) return;
    void navigate({
      to: href, replace: true, state: true,
      search: (current: Record<string, unknown>) => current,
    });
  }, [href, navigate]);

  if (query.error) return <ErrorBanner description={errorMessage(query.error, t("integrations.record.unavailable"))} />;
  if (query.isFetching || href) return <LoadingPanel message={t("integrations.record.loading")} />;
  return <EmptyState icon="integration" title={t("integrations.record.unavailable")} />;
}

export function IntegrationsPage(): React.ReactElement {
  const t = useIntegrateT();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const recordHref = useResourceRecordHrefLookup();
  const groupOptions = React.useMemo(() => [{ id: "concrete_type", label: t("integrations.typeGroup"), group: { field: "concrete_type" }, type: "value" as const }], [t]);
  const rowHref = React.useCallback((row: Row): string => {
    return concreteIntegrationHref(row, recordHref);
  }, [recordHref]);

  return (
    <ResourceList
      resource={MODEL}
      rowHref={rowHref}
      hideCreate
      toolbarActions={<Button variant="primary" onClick={() => void navigate({ to: routeHref("integrate.add") })}>{t("integrations.add.title")}</Button>}
    >
      <List
        resource={MODEL}
        fields={["concrete_target.state", "concrete_target.resource", "concrete_target.id"]}
        defaultGroups={{ list: { field: "concrete_type" }, board: { field: "concrete_type" } }}
        groupOptions={groupOptions}
      >
        <Column field="display_name" />
        <Column field="kind" header={t("col.type")} />
        <Column field="vendor.display_name" header={t("col.vendor")} />
        <Column field="lifecycle" widget="statusBadge" />
        <Column field="runtime_status" widget="colorDot" />
        <Column field="credential.display_name" header={t("col.credential")} />
        <Column field="concrete_target.state" header={t("integrations.targetState")} />
        <Column field="last_error" header={t("col.lastError")} />
      </List>
    </ResourceList>
  );
}

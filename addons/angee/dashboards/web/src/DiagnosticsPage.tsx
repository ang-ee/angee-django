import * as React from "react";
import {
  DetailSection,
  EmptyState,
  InlineEmpty,
  MetricStrip,
  Page,
  PageBody,
  PageHeader,
  PaneRow,
  PaneRows,
  useDashboardRegistry,
  useRuntimeAuth,
} from "@angee/ui";
import { useDashboardsT } from "./i18n";

export function DashboardDiagnosticsPage(): React.ReactElement {
  const registry = useDashboardRegistry();
  const auth = useRuntimeAuth();
  const t = useDashboardsT();
  if (!auth.hasRole("angee/role:admin")) {
    return <EmptyState icon="dashboard" title={t("diagnostics.unavailable.title")} description={t("diagnostics.unavailable.description")} />;
  }
  const definitions = Object.values(registry.definitions);
  return (
    <Page>
      <PageHeader title={t("diagnostics.title")} description={t("diagnostics.description")} />
      <PageBody className="space-y-4">
        <MetricStrip metrics={[
          { label: t("diagnostics.definitions"), numericValue: definitions.length, icon: "dashboard" },
          { label: t("diagnostics.widgetKinds"), numericValue: Object.keys(registry.widgetKinds).length, icon: "grid" },
          { label: t("diagnostics.persistence"), value: registry.store ? t("diagnostics.installed") : t("diagnostics.unavailable"), icon: "archive" },
        ]} />
        <DetailSection title={t("diagnostics.registered")}>
          {definitions.length ? (
            <PaneRows>
              {definitions.map((definition) => (
                <li key={definition.key}>
                  <PaneRow icon="dashboard" title={definition.title} detail={definition.key} meta={t("diagnostics.revision", { revision: definition.revision })} />
                </li>
              ))}
            </PaneRows>
          ) : <InlineEmpty label={t("diagnostics.none")} />}
        </DetailSection>
      </PageBody>
    </Page>
  );
}

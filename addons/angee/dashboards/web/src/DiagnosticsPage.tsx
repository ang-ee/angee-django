import * as React from "react";
import {
  Card,
  EmptyState,
  Page,
  PageBody,
  PageHeader,
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
  return (
    <Page>
      <PageHeader title={t("diagnostics.title")} description={t("diagnostics.description")} />
      <PageBody className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        <DiagnosticCard label={t("diagnostics.definitions")} value={Object.keys(registry.definitions).length} />
        <DiagnosticCard label={t("diagnostics.widgetKinds")} value={Object.keys(registry.widgetKinds).length} />
        <DiagnosticCard label={t("diagnostics.persistence")} value={registry.store ? t("diagnostics.installed") : t("diagnostics.unavailable")} />
      </div>
      <Card className="p-4">
        <h2 className="mb-2 text-14 font-semibold text-fg">{t("diagnostics.registered")}</h2>
        <ul className="space-y-1 text-13 text-fg-muted">
          {Object.values(registry.definitions).map((definition) => (
            <li key={definition.key}><span className="font-medium text-fg">{definition.title}</span> · {definition.key} · {t("diagnostics.revision", { revision: definition.revision })}</li>
          ))}
        </ul>
      </Card>
      </PageBody>
    </Page>
  );
}

function DiagnosticCard({ label, value }: { label: string; value: React.ReactNode }): React.ReactElement {
  return <Card className="p-4"><p className="text-12 text-fg-muted">{label}</p><p className="mt-1 text-xl font-semibold text-fg">{value}</p></Card>;
}

import * as React from "react";
import { Card, EmptyState, useDashboardRegistry, useRuntimeAuth } from "@angee/ui";

export function DashboardDiagnosticsPage(): React.ReactElement {
  const registry = useDashboardRegistry();
  const auth = useRuntimeAuth();
  if (!auth.hasRole("angee/role:admin")) {
    return <EmptyState icon="dashboard" title="Diagnostics unavailable" description="Dashboard diagnostics require platform administration access." />;
  }
  return (
    <main className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-4">
      <header>
        <h1 className="text-xl font-semibold text-fg">Dashboard diagnostics</h1>
        <p className="text-13 text-fg-muted">Composed declarations, widget kinds, and persistence availability.</p>
      </header>
      <div className="grid gap-3 md:grid-cols-3">
        <DiagnosticCard label="Definitions" value={Object.keys(registry.definitions).length} />
        <DiagnosticCard label="Widget kinds" value={Object.keys(registry.widgetKinds).length} />
        <DiagnosticCard label="Persistence" value={registry.store ? "Installed" : "Unavailable"} />
      </div>
      <Card className="p-4">
        <h2 className="mb-2 text-14 font-semibold text-fg">Registered definitions</h2>
        <ul className="space-y-1 text-13 text-fg-muted">
          {Object.values(registry.definitions).map((definition) => (
            <li key={definition.key}><span className="font-medium text-fg">{definition.title}</span> · {definition.key} · revision {definition.revision}</li>
          ))}
        </ul>
      </Card>
    </main>
  );
}

function DiagnosticCard({ label, value }: { label: string; value: React.ReactNode }): React.ReactElement {
  return <Card className="p-4"><p className="text-12 text-fg-muted">{label}</p><p className="mt-1 text-xl font-semibold text-fg">{value}</p></Card>;
}

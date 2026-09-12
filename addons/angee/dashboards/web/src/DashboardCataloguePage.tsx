import * as React from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  Button,
  Card,
  EmptyState,
  Input,
  TextLink,
  useDashboardRegistry,
  useRouteHref,
} from "@angee/ui";
import type { DashboardRegistry, DashboardSummary, DashboardTarget } from "@angee/ui/dashboard/headless";

export function DashboardCataloguePage(): React.ReactElement {
  const registry = useDashboardRegistry();
  const store = registry.store;
  if (!store) {
    return <EmptyState icon="dashboard" title="Dashboards unavailable" description="No dashboard persistence adapter is installed." />;
  }
  return <DashboardCatalogue store={store} registry={registry} />;
}

function targetKey(target: DashboardTarget): string {
  return target.scope === "personal" ? `personal:${target.id}` : `${target.scope}:${target.key}`;
}

function DashboardCatalogue({ store, registry }: {
  store: NonNullable<ReturnType<typeof useDashboardRegistry>["store"]>;
  registry: DashboardRegistry;
}): React.ReactElement {
  const catalogue = store.useCatalogue();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const [name, setName] = React.useState("");
  const [creating, setCreating] = React.useState(false);
  const [error, setError] = React.useState<Error | null>(null);
  const summaries = React.useMemo<readonly DashboardSummary[]>(() => {
    const merged = new Map(catalogue.summaries.map((summary) => [targetKey(summary.target), summary]));
    for (const definition of Object.values(registry.definitions)) {
      const target: DashboardTarget = definition.resource
        ? { scope: "resource", key: definition.resource }
        : { scope: "addon", key: definition.key };
      const key = targetKey(target);
      if (merged.has(key)) continue;
      const resources = [...new Set(definition.widgets.flatMap((widget) =>
        widget.data.shape === "none" ? [] : [widget.data.source.resource],
      ))].sort();
      merged.set(key, {
        id: definition.key,
        target,
        title: definition.title,
        resources,
        revision: 0,
        customized: false,
        available: true,
        capabilities: { canEdit: true, canReset: false, canShare: false, canArchive: false },
      });
    }
    return [...merged.values()].sort((left, right) => left.title.localeCompare(right.title));
  }, [catalogue.summaries, registry.definitions]);

  const create = async () => {
    if (!name.trim()) return;
    setCreating(true);
    setError(null);
    try {
      const result = await catalogue.createPersonal({
        name: name.trim(),
        clientCreationKey: globalThis.crypto?.randomUUID?.() ?? `dashboard-${Date.now()}`,
      });
      await navigate({ to: routeHref("dashboards.detail", { id: result.persistedId }) });
    } catch (cause) {
      setError(cause instanceof Error ? cause : new Error(String(cause)));
    } finally {
      setCreating(false);
    }
  };

  return (
    <main className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto p-4">
      <header className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold text-fg">Dashboards</h1>
          <p className="text-13 text-fg-muted">Personal dashboards and your customized addon and resource views.</p>
        </div>
        <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="Dashboard name" aria-label="Dashboard name" className="w-56" />
        <Button type="button" variant="primary" disabled={creating || !name.trim()} onClick={() => void create()}>
          {creating ? "Creating…" : "Create dashboard"}
        </Button>
      </header>
      {error || catalogue.error ? <p role="alert" className="text-13 text-danger-text">{(error ?? catalogue.error)?.message}</p> : null}
      {catalogue.loading && summaries.length === 0 ? <p className="text-13 text-fg-muted">Loading dashboards…</p> : null}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {summaries.filter((dashboard) => dashboard.available).map((dashboard) => {
          const href = dashboard.target.scope === "personal"
            ? routeHref("dashboards.detail", { id: dashboard.target.id })
            : dashboard.target.scope === "resource"
              ? routeHref("dashboards.resource", { key: dashboard.target.key })
              : routeHref("dashboards.addon", { key: dashboard.target.key });
          return (
            <Card key={`${dashboard.target.scope}:${dashboard.id}`} className="flex min-h-32 flex-col gap-2 p-4">
              <TextLink href={href} variant="muted" className="text-15 font-semibold text-fg">{dashboard.title}</TextLink>
              <p className="line-clamp-2 text-13 text-fg-muted">{dashboard.description || "No description"}</p>
              <div className="mt-auto flex flex-wrap gap-1 text-2xs text-fg-subtle">
                <span>{dashboard.target.scope}</span>
                {dashboard.resources.map((resource) => <span key={resource}>· {resource}</span>)}
              </div>
            </Card>
          );
        })}
      </div>
      {!catalogue.loading && summaries.length === 0 ? (
        <EmptyState icon="dashboard" title="No dashboards yet" description="Create a personal dashboard, or customize a resource dashboard." />
      ) : null}
    </main>
  );
}

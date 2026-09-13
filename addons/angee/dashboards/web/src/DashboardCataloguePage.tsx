import * as React from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  Button,
  Card,
  EmptyState,
  ErrorBanner,
  Input,
  LoadingPanel,
  Page,
  PageBody,
  PageHeader,
  PageToolbar,
  Select,
  TextLink,
  useDashboardRegistry,
  useRouteHref,
} from "@angee/ui";
import type { DashboardRegistry, DashboardSummary, DashboardTarget } from "@angee/ui/dashboard/headless";
import { useDashboardsT } from "./i18n";

export function DashboardCataloguePage(): React.ReactElement {
  const t = useDashboardsT();
  const registry = useDashboardRegistry();
  const store = registry.store;
  if (!store) {
    return <EmptyState icon="dashboard" title={t("catalogue.unavailable.title")} description={t("catalogue.unavailable.description")} />;
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
  const [query, setQuery] = React.useState("");
  const [sort, setSort] = React.useState<"name" | "scope">("name");
  const t = useDashboardsT();
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
        capabilities: { canEdit: true, canReset: false, canArchive: false },
      });
    }
    return [...merged.values()].sort((left, right) => left.title.localeCompare(right.title));
  }, [catalogue.summaries, registry.definitions]);
  const visibleSummaries = React.useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    return summaries
      .filter((dashboard) => dashboard.available)
      .filter((dashboard) =>
        !needle
        || dashboard.title.toLocaleLowerCase().includes(needle)
        || dashboard.description?.toLocaleLowerCase().includes(needle)
        || dashboard.resources.some((resource) => resource.toLocaleLowerCase().includes(needle)),
      )
      .sort((left, right) => {
        if (sort === "scope") {
          const scopeOrder = left.target.scope.localeCompare(right.target.scope);
          if (scopeOrder !== 0) return scopeOrder;
        }
        return left.title.localeCompare(right.title);
      });
  }, [query, sort, summaries]);

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
    <Page>
      <PageHeader
        title={t("catalogue.title")}
        description={t("catalogue.description")}
      />
      <PageToolbar
        start={(
          <>
            <Input value={name} onChange={(event) => setName(event.target.value)} placeholder={t("catalogue.name")} aria-label={t("catalogue.name")} className="min-w-40 flex-1 sm:w-56 sm:flex-none" />
            <Button type="button" variant="primary" disabled={creating || !name.trim()} onClick={() => void create()}>
              {creating ? t("common.creating") : t("common.create")}
            </Button>
          </>
        )}
        end={(
          <>
            <Input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t("catalogue.search")} aria-label={t("catalogue.search")} className="min-w-40 flex-1 sm:w-56 sm:flex-none" />
            <Select
              value={sort}
              aria-label={t("catalogue.sort")}
              className="w-32"
              options={[
                { value: "name", label: t("catalogue.sort.name") },
                { value: "scope", label: t("catalogue.sort.scope") },
              ]}
              onValueChange={(value) => setSort(value as "name" | "scope")}
            />
          </>
        )}
        className="flex-wrap"
      />
      <ErrorBanner description={(error ?? catalogue.error)?.message ?? null} />
      <PageBody className="space-y-4">
      {catalogue.loading && summaries.length === 0 ? <LoadingPanel message={t("common.loading")} /> : null}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {visibleSummaries.map((dashboard) => {
          const href = dashboard.target.scope === "personal"
            ? routeHref("dashboards.detail", { id: dashboard.target.id })
            : dashboard.target.scope === "resource"
              ? routeHref("dashboards.resource", { key: dashboard.target.key })
              : routeHref("dashboards.addon", { key: dashboard.target.key });
          return (
            <Card key={`${dashboard.target.scope}:${dashboard.id}`} className="flex min-h-32 flex-col gap-2 p-4">
              <TextLink href={href} variant="muted" className="text-15 font-semibold text-fg">{dashboard.title}</TextLink>
              <p className="line-clamp-2 text-13 text-fg-muted">{dashboard.description || t("common.noDescription")}</p>
              <div className="mt-auto flex flex-wrap gap-1 text-2xs text-fg-subtle">
                <span>{dashboard.target.scope}</span>
                {dashboard.resources.map((resource) => <span key={resource}>· {resource}</span>)}
              </div>
            </Card>
          );
        })}
      </div>
      {!catalogue.loading && visibleSummaries.length === 0 ? (
        <EmptyState icon="dashboard" title={t("catalogue.empty.title")} description={t("catalogue.empty.description")} />
      ) : null}
      </PageBody>
    </Page>
  );
}

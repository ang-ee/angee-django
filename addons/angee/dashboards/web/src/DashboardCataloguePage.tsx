import * as React from "react";
import { useModelMetadata } from "@angee/metadata";
import { useNavigate } from "@tanstack/react-router";
import {
  Button,
  Chip,
  EmptyState,
  Glyph,
  MutationDialog,
  OverflowChipList,
  Page,
  PageBody,
  PageHeader,
  RemovedRecords,
  RowsListView,
  mutationDialogValueCodecs,
  textRoleVariants,
  useDashboardRegistry,
  useAppRuntime,
  useRouteHref,
  type DescriptorField,
  type ListColumn,
  type MutationDialogValues,
  type StringIdRow,
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

/** The scope labels, one static key per scope so the i18n guard sees them. */
const SCOPE_LABEL = {
  personal: "catalogue.scope.personal",
  resource: "catalogue.scope.resource",
  addon: "catalogue.scope.addon",
} as const;

function targetKey(target: DashboardTarget): string {
  return target.scope === "personal" ? `personal:${target.id}` : `${target.scope}:${target.key}`;
}

/** One catalogue row: a stored or declared dashboard with its destination. */
interface DashboardRow extends StringIdRow {
  title: string;
  description: string;
  scope: DashboardTarget["scope"];
  resources: readonly string[];
  href: string;
}

function DashboardCatalogue({ store, registry }: {
  store: NonNullable<ReturnType<typeof useDashboardRegistry>["store"]>;
  registry: DashboardRegistry;
}): React.ReactElement {
  const catalogue = store.useCatalogue();
  const dashboardResource = useModelMetadata("dashboards.Dashboard")?.resource;
  const { resourceViews } = useAppRuntime();
  const navigate = useNavigate();
  const routeHref = useRouteHref();
  const t = useDashboardsT();
  // Stored summaries first, then the declared addon and resource dashboards no one
  // has customized yet, so every dashboard the viewer can open is one row.
  const summaries = React.useMemo<readonly DashboardSummary[]>(() => {
    const presetResource = (preset: string) => resourceViews[preset]?.resource ?? null;
    const merged = new Map(catalogue.summaries.map((summary) => [targetKey(summary.target), {
      ...summary,
      resources: [...new Set([...summary.resources, ...(summary.presets ?? []).flatMap((preset) => {
        const resource = presetResource(preset);
        return resource ? [resource] : [];
      })])].sort(),
    }]));
    for (const definition of Object.values(registry.definitions)) {
      const target: DashboardTarget = definition.resource
        ? { scope: "resource", key: definition.resource }
        : { scope: "addon", key: definition.key };
      const key = targetKey(target);
      if (merged.has(key)) continue;
      const resources = [...new Set(definition.widgets.flatMap((widget) => {
        if (widget.data.shape === "none") return [];
        if (widget.data.shape === "resourceView") {
          const resource = presetResource(widget.data.preset);
          return resource ? [resource] : [];
        }
        return [widget.data.source.resource];
      }))].sort();
      merged.set(key, {
        id: definition.key,
        target,
        title: definition.title,
        resources,
        revision: 0,
        customized: false,
        available: true,
        capabilities: { canEdit: definition.editable !== false, canReset: false, canTrash: false },
      });
    }
    return [...merged.values()];
  }, [catalogue.summaries, registry.definitions, resourceViews]);
  const rows = React.useMemo<readonly DashboardRow[]>(() => summaries
    .filter((dashboard) => dashboard.available)
    .map((dashboard) => ({
      id: targetKey(dashboard.target),
      title: dashboard.title,
      description: dashboard.description ?? "",
      scope: dashboard.target.scope,
      resources: dashboard.resources,
      href: dashboard.target.scope === "personal"
        ? routeHref("dashboards.detail", { id: dashboard.target.id })
        : dashboard.target.scope === "resource"
          ? routeHref("dashboards.resource", { key: dashboard.target.key })
          : routeHref("dashboards.addon", { key: dashboard.target.key }),
    })), [routeHref, summaries]);
  const columns = React.useMemo<readonly ListColumn<DashboardRow>[]>(() => [
    { field: "title", header: t("catalogue.col.title"), subline: "description" },
    { field: "scope", header: t("catalogue.col.scope"), render: (row) => <Chip tone="neutral" size="sm">{t(SCOPE_LABEL[row.scope])}</Chip> },
    { field: "resources", header: t("catalogue.col.resources"), sortable: false, render: (row) => <OverflowChipList items={row.resources.map((resource) => ({ id: resource, label: resource }))} max={4} /> },
  ], [t]);
  const createFields = React.useMemo<readonly DescriptorField[]>(() => [
    { name: "name", label: t("catalogue.name"), required: true },
  ], [t]);

  return (
    <Page>
      <PageHeader
        title={t("catalogue.title")}
        description={t("catalogue.description")}
        actions={(
          <MutationDialog
            trigger={<Button variant="primary" size="sm"><Glyph decorative name="plus" />{t("common.create")}</Button>}
            title={t("common.create")}
            fields={createFields}
            submitLabel={t("common.create")}
            submittingLabel={t("common.creating")}
            parseValues={(values: MutationDialogValues) => ({ name: mutationDialogValueCodecs.requiredString(values.name, "name") })}
            onSubmit={async ({ name }) => {
              const result = await catalogue.createPersonal({
                name,
                idempotencyKey: globalThis.crypto?.randomUUID?.() ?? `dashboard-${Date.now()}`,
              });
              return { status: "ok", data: result };
            }}
            onSubmitted={(result) => { void navigate({ to: routeHref("dashboards.detail", { id: result.persistedId }) }); }}
          />
        )}
      />
      <PageBody className="space-y-4">
        <RowsListView<DashboardRow>
          rows={rows}
          columns={columns}
          fetching={catalogue.loading && rows.length === 0}
          error={catalogue.error ?? null}
          search={{ box: true }}
          groupOptions={[{ id: "scope", label: t("catalogue.col.scope"), group: { field: "scope" }, type: "value" }]}
          rowHref={(row) => row.href}
          gallery={{
            title: "title",
            subtitle: "description",
            renderCard: (row) => (
              <div className="grid min-w-0 gap-2">
                <span className={textRoleVariants({ role: "title", truncate: true })}>{row.title}</span>
                <span className={textRoleVariants({ role: "description" })}>{row.description || t("common.noDescription")}</span>
                <span className="flex flex-wrap items-center gap-1">
                  <Chip tone="neutral" size="sm">{t(SCOPE_LABEL[row.scope])}</Chip>
                  <OverflowChipList items={row.resources.map((resource) => ({ id: resource, label: resource }))} max={3} />
                </span>
              </div>
            ),
          }}
          emptyContent={{ icon: "dashboard", title: t("catalogue.empty.title"), description: t("catalogue.empty.description") }}
        />
        <RemovedRecords resource={dashboardResource} label={(row) => String(row.name ?? row.id)} fields={["name"]} />
      </PageBody>
    </Page>
  );
}

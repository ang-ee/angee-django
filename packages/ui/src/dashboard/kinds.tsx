import * as React from "react";
import { rowPublicId, useModelMetadata } from "@angee/metadata";
import * as v from "valibot";
import { MetricTile } from "../fragments/MetricStrip";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { InlineEmpty } from "../fragments/InlineEmpty";
import { NavLink } from "../ui/nav-link";
import { Skeleton, SkeletonStatus } from "../ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table";
import { widgetColumns, type DashboardWidgetKind, type DashboardWidgetRenderProps, type WidgetColumn } from "./headless";
import { DashboardBars, DashboardDonut } from "./charts";
import { useDashboardT } from "./i18n";
import { titleCase } from "../lib/titleCase";
import { fieldLabel } from "../views/resource/model-metadata-defaults";
import { ResourceViewProvider } from "../views/resource/resource-view-context";
import { resourceViewPresetDefaults } from "../views/resource/model/favorites";
import { useAppRuntime } from "../runtime/runtime";
import { useUiT } from "../i18n";
import type { ColumnDescriptor } from "../views/page";
import { cellContent } from "../views/resource/list-body/cell-utils";
import { useResourceRecordHrefLookup, useRouteHref } from "../runtime/runtime";

function DataState({ data, children, skeleton }: DashboardWidgetRenderProps & { children: React.ReactNode; skeleton: React.ReactNode }): React.ReactElement {
  const t = useDashboardT();
  if (data.error) return <ErrorBanner description={data.error.message} />;
  if (data.fetching && data.value == null && data.series.length === 0 && data.rows.length === 0) {
    return <SkeletonStatus label={t("widget.loading")} className="flex h-full flex-col gap-3 p-2">{skeleton}</SkeletonStatus>;
  }
  return <>{children}</>;
}

function StatWidget(props: DashboardWidgetRenderProps): React.ReactElement {
  const suffix = typeof props.spec.options.suffix === "string" ? props.spec.options.suffix : "";
  return (
    <DataState {...props} skeleton={<><Skeleton className="h-5 w-1/2" /><Skeleton className="h-8 w-2/3" /></>}>
      <MetricTile
        className="h-full border-0 bg-transparent p-0 shadow-none"
        density="compact"
        format={{ maximumFractionDigits: 2 }}
        label={<span className="sr-only">{props.spec.title}</span>}
        numericValue={props.data.value ?? undefined}
        suffix={suffix}
        value="—"
        valueSize="lg"
      />
    </DataState>
  );
}

function BarWidget(props: DashboardWidgetRenderProps): React.ReactElement {
  return <DataState {...props} skeleton={<><Skeleton className="h-4 w-1/3" /><Skeleton className="h-6 w-4/5" /><Skeleton className="h-6 w-3/5" /><Skeleton className="h-6 w-2/5" /></>}><DashboardBars points={props.data.series} title={props.spec.title} /></DataState>;
}

function DonutWidget(props: DashboardWidgetRenderProps): React.ReactElement {
  return <DataState {...props} skeleton={<Skeleton shape="avatar" className="mx-auto size-36" />}><DashboardDonut points={props.data.series} title={props.spec.title} /></DataState>;
}

function TableWidget(props: DashboardWidgetRenderProps): React.ReactElement {
  const t = useDashboardT();
  const uiT = useUiT();
  const recordHref = useResourceRecordHrefLookup();
  const routeHref = useRouteHref();
  const metadata = useModelMetadata(props.spec.data.shape === "rows" ? props.spec.data.source.resource : "");
  const recordRoute = typeof props.spec.options.recordRoute === "string" ? props.spec.options.recordRoute : undefined;
  const recordParam = typeof props.spec.options.recordParam === "string" ? props.spec.options.recordParam : "id";
  const identityField = props.data.identity?.field;
  const defaultColumns = React.useMemo<WidgetColumn[]>(() => {
    const fields = props.spec.data.shape === "rows"
      ? props.spec.data.source.fields ?? (identityField ? [identityField] : [])
      : [];
    const visible = fields
      .filter((field) => field !== identityField && props.data.queryFields[field]?.row)
      .map((path) => ({ path }));
    return visible.length ? visible : identityField && props.data.queryFields[identityField]?.row ? [{ path: identityField }] : [];
  }, [props.spec.data, props.data.queryFields, identityField]);
  const { columns, error } = React.useMemo(() => {
    const parsed = widgetColumns({ options: props.spec.options });
    if (!parsed.success) return { columns: [], error: new v.ValiError(parsed.issues) };
    const columns = (parsed.output ?? defaultColumns).map(({ path, label }) => {
      const queryField = props.data.queryFields[path];
      const column: ColumnDescriptor = { field: queryField?.row?.path ?? path, header: fieldLabel(path, metadata?.fields[path], label ?? titleCase(path)), queryField };
      return { path, column };
    });
    return { columns, error: null };
  }, [defaultColumns, props.spec.options, props.data.queryFields, metadata]);
  const resource = props.data.identity ? { query: { identity: props.data.identity } } : null;
  return (
    <DataState {...props} data={{ ...props.data, error: props.data.error ?? error }} skeleton={<><Skeleton className="h-5 w-full" />{Array.from({ length: 4 }, (_, index) => <Skeleton key={index} className="h-6 w-full" />)}</>}>
      {props.data.rows.length === 0 ? <InlineEmpty label={t("widget.noRows")} /> : (
        <Table density="compact" className="table-fixed" aria-labelledby={props.titleId} aria-label={props.titleId ? undefined : props.spec.title}>
          <TableHeader><TableRow>{columns.map(({ path, column }) => <TableHead key={path} className="max-w-64">{column.header}</TableHead>)}</TableRow></TableHeader>
          <TableBody>
            {props.data.rows.map((row, index) => {
              const id = rowPublicId(row, resource);
              const href = id && props.spec.data.shape === "rows"
                ? recordRoute ? routeHref.maybe(recordRoute, { [recordParam]: id })
                  : recordHref(props.spec.data.source.resource, id, row)
                : undefined;
              return <TableRow key={id ?? index} interactive={Boolean(href)}>{columns.map(({ path, column }, columnIndex) => (
                <TableCell key={path} className="max-w-64 truncate">
                  {href && columnIndex === 0 ? <NavLink href={href} className="block truncate focus-visible:focus-ring">{cellContent(column, row, uiT)}</NavLink> : cellContent(column, row, uiT)}
                </TableCell>
              ))}</TableRow>;
            })}
          </TableBody>
        </Table>
      )}
    </DataState>
  );
}

function AuthoredWidget(props: DashboardWidgetRenderProps): React.ReactElement {
  const t = useDashboardT();
  return <>{props.authored ?? <InlineEmpty label={t("widget.authoredUnavailable")} />}</>;
}

function ResourceViewWidget({ spec, hostedView: View, onCountChange }: DashboardWidgetRenderProps): React.ReactElement {
  const t = useDashboardT();
  const { resourceViews } = useAppRuntime();
  const preset = spec.data.shape === "resourceView" ? resourceViews[spec.data.preset] : undefined;
  if (!preset || !["list", "gantt"].includes(preset.view ?? "list") || !View) {
    return <ErrorBanner description={t("widget.viewUnavailable")} />;
  }
  return <ResourceViewProvider key={preset.id} resource={preset.resource} scope="local" presetIds={[preset.id]} initialState={resourceViewPresetDefaults(undefined, preset)}>
    <View
      presentation="embedded"
      scope="inherit"
      chrome={{ heading: false, search: true, pager: false }}
      onListStateChange={(state) => onCountChange?.(state.total ?? null)}
    />
  </ResourceViewProvider>;
}

export const BUILTIN_DASHBOARD_WIDGET_KINDS: readonly DashboardWidgetKind[] = [
  { id: "stat", contributionId: "angee.stat", version: 1, label: "Statistic", shape: "value", defaultSize: { w: 3, h: 2 }, minSize: { w: 2, h: 2 }, Component: StatWidget },
  { id: "bar", contributionId: "angee.bar", version: 1, label: "Bar chart", shape: "series", defaultSize: { w: 6, h: 4 }, minSize: { w: 3, h: 3 }, Component: BarWidget },
  { id: "donut", contributionId: "angee.donut", version: 1, label: "Donut chart", shape: "series", defaultSize: { w: 6, h: 4 }, minSize: { w: 3, h: 3 }, Component: DonutWidget },
  { id: "table", contributionId: "angee.table", version: 1, label: "Table", shape: "rows", defaultSize: { w: 6, h: 4 }, minSize: { w: 3, h: 3 }, Component: TableWidget },
  { id: "authored", contributionId: "angee.authored", version: 1, label: "Authored panel", shape: "none", defaultSize: { w: 6, h: 4 }, minSize: { w: 2, h: 2 }, Component: AuthoredWidget },
  { id: "resourceView", contributionId: "angee.resourceView", version: 1, label: "Resource view", shape: "resourceView", defaultSize: { w: 12, h: 6 }, minSize: { w: 6, h: 4 }, Component: ResourceViewWidget },
];

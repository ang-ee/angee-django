import * as React from "react";
import { rowPublicId } from "@angee/metadata";
import * as v from "valibot";
import { MetricTile } from "../fragments/MetricStrip";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { InlineEmpty } from "../fragments/InlineEmpty";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table";
import { widgetColumns, type DashboardWidgetKind, type DashboardWidgetRenderProps, type WidgetColumn } from "./headless";
import { DashboardBars, DashboardDonut } from "./charts";
import { useDashboardT } from "./i18n";
import { titleCase } from "../lib/titleCase";
import { useUiT } from "../i18n";
import type { ColumnDescriptor } from "../views/page";
import { cellContent } from "../views/resource/list-body/cell-utils";

function DataState({ data, children }: DashboardWidgetRenderProps & { children: React.ReactNode }): React.ReactElement {
  const t = useDashboardT();
  if (data.error) return <ErrorBanner description={data.error.message} />;
  if (data.fetching && data.value == null && data.series.length === 0 && data.rows.length === 0) {
    return <LoadingPanel density="inline" message={t("widget.loading")} />;
  }
  return <>{children}</>;
}

function StatWidget(props: DashboardWidgetRenderProps): React.ReactElement {
  const t = useDashboardT();
  const suffix = typeof props.spec.options.suffix === "string" ? props.spec.options.suffix : "";
  const value = props.data.value == null
    ? "—"
    : `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(props.data.value)}${suffix}`;
  return (
    <DataState {...props}>
      <MetricTile
        className="h-full border-0 bg-transparent p-0 shadow-none"
        density="compact"
        label={t("widget.value")}
        value={value}
        valueClassName="text-xl font-semibold leading-6 tabular-nums"
      />
    </DataState>
  );
}

function BarWidget(props: DashboardWidgetRenderProps): React.ReactElement {
  return <DataState {...props}><DashboardBars points={props.data.series} title={props.spec.title} /></DataState>;
}

function DonutWidget(props: DashboardWidgetRenderProps): React.ReactElement {
  return <DataState {...props}><DashboardDonut points={props.data.series} title={props.spec.title} /></DataState>;
}

function TableWidget(props: DashboardWidgetRenderProps): React.ReactElement {
  const t = useDashboardT();
  const uiT = useUiT();
  const identityField = props.data.identity?.field;
  const defaultColumns = React.useMemo<WidgetColumn[]>(() => {
    const fields = props.spec.data.shape === "rows"
      ? props.spec.data.source.fields ?? (identityField ? [identityField] : [])
      : [];
    return fields
      .filter((field) => field !== identityField && props.data.queryFields[field]?.row)
      .map((path) => ({ path }));
  }, [props.spec.data, props.data.queryFields, identityField]);
  const { columns, error } = React.useMemo(() => {
    const parsed = widgetColumns({ options: props.spec.options });
    if (!parsed.success) return { columns: [], error: new v.ValiError(parsed.issues) };
    const columns = (parsed.output ?? defaultColumns).map(({ path, label }) => {
      const queryField = props.data.queryFields[path];
      const column: ColumnDescriptor = { field: queryField?.row?.path ?? path, header: label ?? titleCase(path), queryField };
      return { path, column };
    });
    return { columns, error: null };
  }, [defaultColumns, props.spec.options, props.data.queryFields]);
  const resource = props.data.identity ? { query: { identity: props.data.identity } } : null;
  return (
    <DataState {...props} data={{ ...props.data, error: props.data.error ?? error }}>
      {props.data.rows.length === 0 ? <InlineEmpty label={t("widget.noRows")} /> : (
        <Table density="compact" className="table-fixed" aria-labelledby={props.titleId} aria-label={props.titleId ? undefined : props.spec.title}>
          <TableHeader><TableRow>{columns.map(({ path, column }) => <TableHead key={path} className="max-w-64">{column.header}</TableHead>)}</TableRow></TableHeader>
          <TableBody>
            {props.data.rows.map((row, index) => (
              <TableRow key={rowPublicId(row, resource) ?? index}>{columns.map(({ path, column }) => (
                <TableCell key={path} className="max-w-64 truncate">
                  {cellContent(column, row, uiT)}
                </TableCell>
              ))}</TableRow>
            ))}
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

export const BUILTIN_DASHBOARD_WIDGET_KINDS: readonly DashboardWidgetKind[] = [
  { id: "stat", contributionId: "angee.stat", version: 1, label: "Statistic", shape: "value", defaultSize: { w: 3, h: 2 }, minSize: { w: 2, h: 2 }, Component: StatWidget },
  { id: "bar", contributionId: "angee.bar", version: 1, label: "Bar chart", shape: "series", defaultSize: { w: 6, h: 4 }, minSize: { w: 3, h: 3 }, Component: BarWidget },
  { id: "donut", contributionId: "angee.donut", version: 1, label: "Donut chart", shape: "series", defaultSize: { w: 6, h: 4 }, minSize: { w: 3, h: 3 }, Component: DonutWidget },
  { id: "table", contributionId: "angee.table", version: 1, label: "Table", shape: "rows", defaultSize: { w: 6, h: 4 }, minSize: { w: 3, h: 3 }, Component: TableWidget },
  { id: "authored", contributionId: "angee.authored", version: 1, label: "Authored panel", shape: "none", defaultSize: { w: 6, h: 4 }, minSize: { w: 2, h: 2 }, Component: AuthoredWidget },
];

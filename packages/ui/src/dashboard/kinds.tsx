import * as React from "react";
import { MetricTile } from "../fragments/MetricStrip";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { InlineEmpty } from "../fragments/InlineEmpty";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../ui/table";
import type { DashboardWidgetKind, DashboardWidgetRenderProps } from "./headless";
import { DashboardBars, DashboardDonut } from "./charts";
import { useDashboardT } from "./i18n";

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
        className="h-full border-0 bg-transparent p-0 shadow-none [&>div:first-child]:mb-1"
        density="prominent"
        label={t("widget.value")}
        value={value}
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
  const columns = React.useMemo(() => {
    const first = props.data.rows[0];
    return first ? Object.keys(first).filter((key) => key !== "id") : [];
  }, [props.data.rows]);
  return (
    <DataState {...props}>
      {props.data.rows.length === 0 ? <InlineEmpty label={t("widget.noRows")} /> : (
        <Table density="compact">
          <TableHeader><TableRow>{columns.map((column) => <TableHead key={column}>{column.replaceAll("_", " ")}</TableHead>)}</TableRow></TableHeader>
          <TableBody>
            {props.data.rows.map((row, index) => (
              <TableRow key={String(row.id ?? index)}>{columns.map((column) => <TableCell key={column}>{cellText(row[column])}</TableCell>)}</TableRow>
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

function cellText(value: unknown): string {
  if (value == null) return "—";
  if (typeof value !== "object") return String(value);
  const row = value as Record<string, unknown>;
  return String(row.name ?? row.label ?? row.title ?? row.id ?? "—");
}

export const BUILTIN_DASHBOARD_WIDGET_KINDS: readonly DashboardWidgetKind[] = [
  { id: "stat", contributionId: "angee.stat", version: 1, label: "Statistic", shape: "value", defaultSize: { w: 3, h: 2 }, minSize: { w: 2, h: 2 }, Component: StatWidget },
  { id: "bar", contributionId: "angee.bar", version: 1, label: "Bar chart", shape: "series", defaultSize: { w: 6, h: 4 }, minSize: { w: 3, h: 3 }, Component: BarWidget },
  { id: "donut", contributionId: "angee.donut", version: 1, label: "Donut chart", shape: "series", defaultSize: { w: 6, h: 4 }, minSize: { w: 3, h: 3 }, Component: DonutWidget },
  { id: "table", contributionId: "angee.table", version: 1, label: "Table", shape: "rows", defaultSize: { w: 6, h: 4 }, minSize: { w: 3, h: 3 }, Component: TableWidget },
  { id: "authored", contributionId: "angee.authored", version: 1, label: "Authored panel", shape: "none", defaultSize: { w: 6, h: 4 }, minSize: { w: 2, h: 2 }, Component: AuthoredWidget },
];

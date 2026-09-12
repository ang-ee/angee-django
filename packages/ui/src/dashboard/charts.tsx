import * as React from "react";

export interface DashboardSeriesPoint {
  key: string;
  label: string;
  value: number;
}

const SERIES_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
  "var(--chart-7)",
  "var(--chart-8)",
] as const;

function finitePoints(points: readonly DashboardSeriesPoint[]): DashboardSeriesPoint[] {
  return points.filter((point) => Number.isFinite(point.value));
}

function seriesColor(point: DashboardSeriesPoint, index: number): string {
  return point.key === "__other__"
    ? "var(--chart-other)"
    : (SERIES_COLORS[index % SERIES_COLORS.length] ?? "var(--chart-1)");
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
}

function AccessibleSeriesTable({ points }: { points: readonly DashboardSeriesPoint[] }): React.ReactElement {
  return (
    <table className="sr-only">
      <thead><tr><th>Category</th><th>Value</th></tr></thead>
      <tbody>
        {points.map((point) => (
          <tr key={point.key}><th>{point.label}</th><td>{formatNumber(point.value)}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export function DashboardBars({
  points,
  title,
  emptyLabel = "No data",
}: {
  points: readonly DashboardSeriesPoint[];
  title: string;
  emptyLabel?: string;
}): React.ReactElement {
  const values = finitePoints(points);
  const extent = values.reduce((max, point) => Math.max(max, Math.abs(point.value)), 0);
  if (values.length === 0 || extent === 0) {
    return <p className="text-12 text-fg-muted">{emptyLabel}</p>;
  }
  return (
    <figure aria-label={title} className="min-w-0">
      <figcaption className="sr-only">{title}</figcaption>
      <ul className="grid gap-2.5" aria-hidden>
        {values.map((point, index) => (
          <li key={point.key} className="grid min-w-0 grid-cols-[minmax(0,9rem)_1fr] items-center gap-3">
            <span className="truncate text-12 text-fg" title={point.label}>{point.label}</span>
            <span className="relative grid min-w-0 grid-cols-2 items-center">
              <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
              <span className={point.value < 0 ? "col-start-1 flex justify-end" : "col-start-2"}>
                <span
                  className="block h-[18px] min-w-[3px] rounded-4"
                  style={{
                    width: `${Math.max((Math.abs(point.value) / extent) * 100, 1.5)}%`,
                    backgroundColor: seriesColor(point, index),
                  }}
                />
              </span>
              <span className={point.value < 0
                ? "col-start-1 row-start-1 mr-1 justify-self-start text-12 text-fg-muted"
                : "col-start-2 row-start-1 ml-1 justify-self-end text-12 text-fg-muted"}
              >
                {formatNumber(point.value)}
              </span>
            </span>
          </li>
        ))}
      </ul>
      <AccessibleSeriesTable points={values} />
    </figure>
  );
}

export function DashboardDonut({
  points,
  title,
  totalLabel,
  emptyLabel = "No data",
}: {
  points: readonly DashboardSeriesPoint[];
  title: string;
  totalLabel?: string;
  emptyLabel?: string;
}): React.ReactElement {
  const values = finitePoints(points).filter((point) => point.value >= 0);
  const total = values.reduce((sum, point) => sum + point.value, 0);
  if (values.length === 0 || total <= 0) {
    return <p className="text-12 text-fg-muted">{emptyLabel}</p>;
  }
  let offset = 0;
  const radius = 72;
  const circumference = 2 * Math.PI * radius;
  return (
    <figure aria-label={title} className="flex min-w-0 flex-wrap items-center gap-5">
      <figcaption className="sr-only">{title}</figcaption>
      <svg viewBox="0 0 180 180" className="size-[168px] shrink-0" aria-hidden>
        <circle cx="90" cy="90" r={radius} fill="none" stroke="var(--chart-surface)" strokeWidth="24" />
        <g transform="rotate(-90 90 90)">
          {values.map((point, index) => {
            const length = (point.value / total) * circumference;
            const item = (
              <circle
                key={point.key}
                cx="90"
                cy="90"
                r={radius}
                fill="none"
                stroke={seriesColor(point, index)}
                strokeWidth="22"
                strokeDasharray={`${Math.max(length - 2, 0.5)} ${circumference}`}
                strokeDashoffset={-offset}
              />
            );
            offset += length;
            return item;
          })}
        </g>
        <text x="90" y="88" textAnchor="middle" className="fill-fg text-[24px] font-semibold">
          {new Intl.NumberFormat(undefined, { notation: "compact" }).format(total)}
        </text>
        {totalLabel ? <text x="90" y="107" textAnchor="middle" className="fill-fg-muted text-[11px]">{totalLabel}</text> : null}
      </svg>
      <ul className="grid min-w-0 flex-1 gap-1.5" aria-hidden>
        {values.map((point, index) => (
          <li key={point.key} className="flex min-w-0 items-center gap-2 text-12">
            <span className="size-2.5 shrink-0 rounded-2" style={{ backgroundColor: seriesColor(point, index) }} />
            <span className="min-w-0 flex-1 truncate text-fg">{point.label}</span>
            <span className="shrink-0 text-fg-muted">{formatNumber(point.value)} <span className="text-fg-subtle">{Math.round(point.value / total * 100)}%</span></span>
          </li>
        ))}
      </ul>
      <AccessibleSeriesTable points={values} />
    </figure>
  );
}

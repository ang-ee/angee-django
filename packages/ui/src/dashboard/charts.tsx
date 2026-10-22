import * as React from "react";
import { cn } from "../lib/cn";
import { formatNumber, numberFormatter } from "../lib/format-number";
import { toneColorVar, type Tone } from "../lib/tones";
import { InlineEmpty } from "../fragments/InlineEmpty";
import { Button } from "../ui/button";
import { Tooltip } from "../ui/tooltip";
import { useDashboardT } from "./i18n";

export interface DashboardSeriesPoint {
  key: string;
  label: string;
  value: number;
  /** Use a semantic tone when color represents an outcome, rather than a category. */
  tone?: Tone;
}

export interface DashboardSeriesDefinition {
  key: string;
  label: string;
  tone?: Tone;
}

export interface DashboardStackedPoint {
  key: string;
  label: string;
  /** Values keyed by the declared series; omitted series have a zero value. */
  values: Readonly<Record<string, number>>;
}

export interface DashboardBarSelection {
  key: string;
  seriesKey?: string;
}

export type DashboardBarsProps = {
  title: string;
  emptyLabel?: string;
  /** Compact horizontal rows fit short dashboard panels without hiding their legend. */
  density?: "comfortable" | "compact";
  /** Input order is preserved, so time buckets remain chronological. */
  orientation?: "horizontal" | "vertical";
  /** The caller owns navigation or filtering; the chart only identifies the chosen bucket. */
  onSelect?: (selection: DashboardBarSelection) => void;
} & (
  | { points: readonly DashboardSeriesPoint[]; series?: undefined }
  | { points: readonly DashboardStackedPoint[]; series: readonly DashboardSeriesDefinition[] }
);

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

function seriesColor(point: DashboardSeriesDefinition, index: number): string {
  if (point.tone) return toneColorVar(point.tone);
  return point.key === "__other__"
    ? "var(--chart-other)"
    : (SERIES_COLORS[index % SERIES_COLORS.length] ?? "var(--chart-1)");
}

const formatChartNumber = numberFormatter({ maximumFractionDigits: 2 });

function AccessibleSeriesTable({ points, series }: {
  points: readonly (DashboardSeriesPoint | DashboardStackedPoint)[];
  series?: readonly DashboardSeriesDefinition[];
}): React.ReactElement {
  const t = useDashboardT();
  return (
    <table className="sr-only">
      <thead><tr><th scope="col">{t("widget.category")}</th>
        {series ? series.map((item) => <th key={item.key} scope="col">{item.label}</th>) : <th scope="col">{t("widget.value")}</th>}
      </tr></thead>
      <tbody>
        {points.map((point) => (
          <tr key={point.key}><th scope="row">{point.label}</th>
            {"values" in point && series
              ? series.map((item) => <td key={item.key}>{formatChartNumber(point.values[item.key] ?? 0)}</td>)
              : "value" in point ? <td>{formatChartNumber(point.value)}</td> : null}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

interface BarSegment {
  key: string;
  label: string;
  value: number;
  color: string;
  seriesKey?: string;
}

function BarSegments({ segments, pointKey, label, positiveExtent, negativeExtent, vertical, compact, onSelect }: {
  segments: readonly BarSegment[];
  pointKey: string;
  label: string;
  positiveExtent: number;
  negativeExtent: number;
  vertical: boolean;
  compact?: boolean;
  onSelect?: DashboardBarsProps["onSelect"];
}): React.ReactElement {
  const extent = positiveExtent + negativeExtent || 1;
  let positive = negativeExtent;
  let negative = negativeExtent;
  return (
    <span className={cn("relative block min-h-0 min-w-0 rounded-4 bg-inset", vertical ? "h-full w-full" : compact ? "h-4 w-full" : "h-6 w-full")}>
      <span aria-hidden className={cn("absolute bg-border-strong", vertical ? "inset-x-0 h-px" : "inset-y-0 w-px")}
        style={vertical ? { bottom: `${negativeExtent / extent * 100}%` } : { left: `${negativeExtent / extent * 100}%` }} />
      {segments.map((segment) => {
        if (segment.value === 0) return null;
        const start = segment.value >= 0 ? positive : negative + segment.value;
        if (segment.value >= 0) positive += segment.value;
        else negative += segment.value;
        const offset = `${start / extent * 100}%`;
        const size = `${Math.abs(segment.value) / extent * 100}%`;
        const style: React.CSSProperties = {
          backgroundColor: segment.color,
          ...(vertical ? { left: 0, bottom: offset, height: size, width: "100%" } : { top: 0, left: offset, width: size, height: "100%" }),
        };
        const description = `${label}${segment.seriesKey ? ` · ${segment.label}` : ""}: ${formatChartNumber(segment.value)}`;
        return onSelect ? (
          <Tooltip key={segment.key} label={description}>
            <Button
              variant="ghost"
              aria-label={description}
              className="absolute min-h-0 min-w-0 rounded-2 border-0 p-0 hover:brightness-110 focus-visible:z-10"
              style={style}
              onClick={() => onSelect({ key: pointKey, seriesKey: segment.seriesKey })}
            />
          </Tooltip>
        ) : <span key={segment.key} aria-hidden title={description} className="absolute rounded-2" style={style} />;
      })}
    </span>
  );
}

/** Single or stacked bars over a shared zero baseline, with the caller's ordering and native actions.
 * @kind surface
 */
export function DashboardBars(props: DashboardBarsProps): React.ReactElement {
  const t = useDashboardT();
  const { title, emptyLabel, onSelect, series } = props;
  const vertical = props.orientation === "vertical";
  const compact = props.density === "compact";
  const rows = props.points.map((point, index) => {
    const segments: BarSegment[] = "values" in point
      ? (series ?? []).map((item, seriesIndex) => ({
        key: item.key, seriesKey: item.key, label: item.label,
        value: point.values[item.key] ?? 0, color: seriesColor(item, seriesIndex),
      }))
      : [{ ...point, color: seriesColor(point, index) }];
    return { point, segments, total: segments.reduce((sum, item) => sum + item.value, 0) };
  }).filter(({ segments }) => segments.length > 0 && segments.every((segment) => Number.isFinite(segment.value)));
  const positiveExtent = Math.max(0, ...rows.map(({ segments }) => segments.reduce((sum, item) => sum + Math.max(0, item.value), 0)));
  const negativeExtent = Math.max(0, ...rows.map(({ segments }) => segments.reduce((sum, item) => sum + Math.max(0, -item.value), 0)));
  if (rows.length === 0) {
    return <InlineEmpty label={emptyLabel ?? t("widget.noData")} />;
  }
  return (
    <figure aria-label={title} className="flex h-full min-h-0 min-w-0 flex-col gap-4 overflow-auto">
      <figcaption className="sr-only">{title}</figcaption>
      <ul
        className={cn("grid flex-1", compact ? "gap-2" : "gap-3", vertical ? "min-h-24 grid-flow-col auto-cols-[minmax(2.5rem,1fr)]" : "min-h-0")}
        style={vertical ? undefined : { gridTemplateRows: `repeat(${rows.length}, minmax(${compact ? "1rem" : "1.5rem"}, 1fr))` }}
      >
        {rows.map(({ point, segments, total }) => (
          <li key={point.key} className={cn("grid min-h-0 min-w-0 gap-2", vertical
            ? "grid-rows-[auto_minmax(0,1fr)_auto] text-center"
            : "grid-cols-[minmax(0,8rem)_minmax(0,1fr)_auto] items-center")}>
            <span aria-hidden className={cn("truncate text-12 text-fg", vertical && "col-start-1 row-start-3")} title={point.label}>{point.label}</span>
            <BarSegments segments={segments} pointKey={point.key} label={point.label}
              positiveExtent={positiveExtent} negativeExtent={negativeExtent} vertical={vertical} compact={compact} onSelect={onSelect} />
            <span aria-hidden className={cn("text-12 tabular-nums text-fg-muted", vertical && "col-start-1 row-start-1")}>
              {formatChartNumber(total)}
            </span>
          </li>
        ))}
      </ul>
      {series ? <ul className="flex shrink-0 flex-wrap gap-x-4 gap-y-2" aria-hidden>
        {series.map((item, index) => <li key={item.key} className="inline-flex items-center gap-2 text-12 text-fg-muted">
          <span className="size-2.5 rounded-2" style={{ backgroundColor: seriesColor(item, index) }} />{item.label}
        </li>)}
      </ul> : null}
      <AccessibleSeriesTable points={rows.map(({ point }) => point)} series={series} />
    </figure>
  );
}

/**
 * @kind surface
 */
export function DashboardDonut({
  points,
  title,
  totalLabel,
  emptyLabel,
  onSelect,
}: {
  points: readonly DashboardSeriesPoint[];
  title: string;
  totalLabel?: string;
  emptyLabel?: string;
  onSelect?: (key: string) => void;
}): React.ReactElement {
  const t = useDashboardT();
  const values = finitePoints(points).filter((point) => point.value >= 0);
  const total = values.reduce((sum, point) => sum + point.value, 0);
  if (values.length === 0 || total <= 0) {
    return <InlineEmpty label={emptyLabel ?? t("widget.noData")} />;
  }
  let offset = 0;
  const radius = 72;
  const circumference = 2 * Math.PI * radius;
  return (
    <figure aria-label={title} className="flex h-full min-w-0 flex-wrap items-center justify-center gap-4 overflow-auto">
      <figcaption className="sr-only">{title}</figcaption>
      <svg viewBox="0 0 180 180" className="aspect-square w-full max-w-40 shrink-0" aria-hidden>
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
          {formatNumber(total, { notation: "compact" })}
        </text>
        {totalLabel ? <text x="90" y="107" textAnchor="middle" className="fill-fg-muted text-[11px]">{totalLabel}</text> : null}
      </svg>
      <ul className="grid min-w-40 flex-1 gap-1" aria-hidden={onSelect ? undefined : true}>
        {values.map((point, index) => {
          const content = <>
            <span className="size-2.5 shrink-0 rounded-2" style={{ backgroundColor: seriesColor(point, index) }} />
            <span className="min-w-0 flex-1 truncate text-left text-fg">{point.label}</span>
            <span className="shrink-0 tabular-nums text-fg-muted">{formatChartNumber(point.value)} <span className="text-fg-subtle">{Math.round(point.value / total * 100)}%</span></span>
          </>;
          return <li key={point.key}>
            {onSelect ? <Button variant="ghost" size="sm" className="w-full" onClick={() => onSelect(point.key)}>{content}</Button>
              : <span className="flex min-w-0 items-center gap-2 py-1.5 text-12">{content}</span>}
          </li>;
        })}
      </ul>
      <AccessibleSeriesTable points={values} />
    </figure>
  );
}

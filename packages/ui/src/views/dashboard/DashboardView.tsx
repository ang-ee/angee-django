import type { ReactElement, ReactNode } from "react";

import { MetricStrip, type MetricTileValue } from "../../fragments/MetricStrip";
import { cn } from "../../lib/cn";
import type { MetricProps } from "./Metric";
import { pageChildren, pageElementProps } from "../page/types";

/**
 * The aggregate View: authored TSX of `<Metric>` Elements folded into one
 * prominent-density `MetricStrip` band, plus any cards/panels below. Purely
 * presentational — the
 * page supplies the values (a bespoke composite read or several resource
 * hooks) — so it renders standalone as a page body (the overview surfaces) and
 * as a view-switcher peer of a list.
 *
 * It owns only the metric band; remaining children render stacked, so the
 * author controls the panel arrangement below without the View imposing a grid.
 */
export interface DashboardViewProps {
  className?: string;
  children?: ReactNode;
}

export function DashboardView({
  children,
  className,
}: DashboardViewProps): ReactElement {
  const metrics: MetricTileValue[] = [];
  const content: ReactNode[] = [];
  for (const child of pageChildren(children)) {
    const metric = pageElementProps<MetricProps>(child, "metric");
    if (metric) {
      metrics.push({
        label: metric.label,
        value: dashboardMetricValue(metric),
        icon: metric.icon,
        tone: metric.tone,
        detail: metric.detail,
      });
    } else {
      content.push(child);
    }
  }

  return (
    <div className={cn("flex flex-col gap-6", className)}>
      {metrics.length > 0 ? <MetricStrip density="prominent" metrics={metrics} /> : null}
      {content}
    </div>
  );
}

function dashboardMetricValue(metric: MetricProps): ReactNode {
  if (metric.format !== "count") return metric.value;
  if (metric.value == null && metric.loading) return "—";
  const value = typeof metric.value === "number" && Number.isFinite(metric.value)
    ? metric.value
    : 0;
  if (metric.max !== undefined && value > metric.max) {
    return `${metric.max.toLocaleString()}+`;
  }
  return value.toLocaleString();
}

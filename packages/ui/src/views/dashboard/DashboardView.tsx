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

/**
 * @kind surface
 */
export function DashboardView({
  children,
  className,
}: DashboardViewProps): ReactElement {
  const metrics: MetricTileValue[] = [];
  const content: ReactNode[] = [];
  for (const child of pageChildren(children)) {
    const metric = pageElementProps<MetricProps>(child, "metric");
    if (metric) {
      metrics.push(dashboardMetric(metric));
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

/** Counts take the tile's typed numeric path so they format and animate there. */
function dashboardMetric(metric: MetricProps): MetricTileValue {
  const { format, loading, max, ...tile } = metric;
  if (format !== "count") return tile;
  if (tile.value == null && loading) return { ...tile, value: "—" };
  const count = typeof tile.value === "number" && Number.isFinite(tile.value)
    ? tile.value
    : 0;
  const bounded = max !== undefined && count > max;
  return {
    ...tile,
    animate: true,
    numericValue: bounded ? max : count,
    suffix: bounded ? "+" : undefined,
  };
}

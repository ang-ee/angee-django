import type { ReactElement, ReactNode } from "react";

import { MetricGrid, type MetricGridTile } from "../fragments/MetricGrid";
import { cn } from "../lib/cn";
import type { MetricProps } from "./dashboard/Metric";
import { pageChildren, pageElementProps } from "./page/types";

/**
 * The aggregate View: authored TSX of `<Metric>` Elements folded into one
 * `MetricGrid` band, plus any cards/panels below. Purely presentational — the
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
  const metrics: MetricGridTile[] = [];
  const content: ReactNode[] = [];
  for (const child of pageChildren(children)) {
    const metric = pageElementProps<MetricProps>(child, "metric");
    if (metric) {
      metrics.push({
        label: metric.label,
        value: metric.value,
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
      {metrics.length > 0 ? <MetricGrid metrics={metrics} /> : null}
      {content}
    </div>
  );
}

import * as React from "react";

import { renderGlyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { type Tone } from "../lib/tones";
import { tv } from "../lib/variants";
import { Tag } from "../ui/badge";
import { Card } from "../ui/card";

export interface MetricGridTile {
  detail?: React.ReactNode;
  icon?: React.ReactNode | string;
  label: React.ReactNode;
  value: React.ReactNode;
  tone?: Tone;
}

export type MetricGridProps = Omit<
  React.HTMLAttributes<HTMLDListElement>,
  "className"
> & {
  className?: string;
  metrics: readonly MetricGridTile[];
};

export const metricGridVariants = tv({
  slots: {
    root: "grid gap-3 sm:grid-cols-2 lg:grid-cols-4",
    tile: "px-4 py-3 shadow-none",
    header: "mb-3 flex min-w-0 items-center justify-between gap-2",
    icon:
      "grid size-7 shrink-0 place-content-center rounded-md bg-inset text-fg-2 [&_.glyph]:size-3.5 [&>svg]:size-3.5",
    value: "m-0 text-2xl font-semibold tabular-nums text-fg",
    detail: "m-0 mt-1 truncate text-2xs text-fg-muted",
  },
});

export const MetricGrid = React.forwardRef<HTMLDListElement, MetricGridProps>(
  function MetricGrid({ className, metrics, ...props }, ref) {
    const styles = metricGridVariants();

    return (
      <dl ref={ref} className={cn(styles.root(), className)} {...props}>
        {metrics.map((metric, index) => (
          <Card
            key={metricKey(metric, index)}
            asChild
            className={styles.tile()}
            density="sm"
          >
            <div>
              <div className={styles.header()}>
                <dt className="contents">
                  <Tag tone={metric.tone ?? "neutral"}>{metric.label}</Tag>
                </dt>
                {metric.icon ? (
                  <span className={styles.icon()}>
                    {renderGlyph(metric.icon)}
                  </span>
                ) : null}
              </div>
              <dd className={styles.value()}>{metric.value}</dd>
              {metric.detail ? (
                <dd className={styles.detail()}>{metric.detail}</dd>
              ) : null}
            </div>
          </Card>
        ))}
      </dl>
    );
  },
);
MetricGrid.displayName = "MetricGrid";

function metricKey(metric: MetricGridTile, index: number): string {
  return `${String(metric.label)}:${String(metric.value)}:${index}`;
}

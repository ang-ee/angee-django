import * as React from "react";

import { cn } from "../lib/cn";
import { tv } from "../lib/variants";
import { Card } from "../ui/card";
import { IconTile } from "../ui/icon-tile";
import { SectionEyebrow } from "../ui/section-eyebrow";
import { textRoleVariants } from "../ui/text";

export interface MetricTileValue {
  detail?: React.ReactNode;
  icon?: React.ReactNode | string;
  label: React.ReactNode;
  value: React.ReactNode;
  /** When set, the tile is a link to this href (rendered as an `<a>`). */
  href?: string;
  /**
   * Client-side navigation handler for `href` — called on a plain left-click so
   * the consumer routes in-app (the tile keeps the real `href` for middle-click /
   * open-in-new-tab). Omit for a normal full-navigation anchor.
   */
  onNavigate?: (href: string) => void;
}

export type MetricTileProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className"
> &
  MetricTileValue & {
    className?: string;
  };

const NAVIGABLE_TILE =
  "cursor-pointer no-underline outline-none transition hover:ring-2 hover:ring-border-focus focus-visible:focus-ring";

export type MetricStripProps = Omit<
  React.HTMLAttributes<HTMLDListElement>,
  "className"
> & {
  className?: string;
  items?: readonly (readonly [React.ReactNode, React.ReactNode])[];
  metrics?: readonly MetricTileValue[];
};

export const metricStripVariants = tv({
  slots: {
    root: "grid gap-3 sm:grid-cols-2 xl:grid-cols-4",
    tile: "min-w-0 px-3 py-2.5 shadow-none",
    header: "flex min-w-0 items-center justify-between gap-2",
    value: "m-0 mt-1 truncate text-13 font-medium text-fg",
    detail: cn(textRoleVariants({ role: "caption", truncate: true }), "m-0 mt-1"),
  },
});

export const MetricTile = React.forwardRef<HTMLElement, MetricTileProps>(
  function MetricTile(
    { className, detail, icon, label, value, href, onNavigate, onClick, ...props },
    ref,
  ) {
    const styles = metricStripVariants();
    const body = (
      <>
        <div className={styles.header()}>
          <SectionEyebrow as="dt">{label}</SectionEyebrow>
          {icon ? <IconTile icon={icon} size="md" /> : null}
        </div>
        <dd className={styles.value()}>{value}</dd>
        {detail ? <p className={styles.detail()}>{detail}</p> : null}
      </>
    );

    if (href != null) {
      const target = href;
      function handleClick(event: React.MouseEvent<HTMLAnchorElement>): void {
        onClick?.(event);
        if (
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey ||
          !onNavigate
        ) {
          return;
        }
        event.preventDefault();
        onNavigate(target);
      }
      return (
        <Card asChild className={styles.tile({ className: cn(className, NAVIGABLE_TILE) })} density="sm">
          <a
            ref={ref as React.Ref<HTMLAnchorElement>}
            href={target}
            onClick={handleClick}
            {...props}
          >
            {body}
          </a>
        </Card>
      );
    }

    return (
      <Card asChild className={styles.tile({ className })} density="sm">
        <div ref={ref as React.Ref<HTMLDivElement>} onClick={onClick} {...props}>
          {body}
        </div>
      </Card>
    );
  },
);
MetricTile.displayName = "MetricTile";

export const MetricStrip = React.forwardRef<HTMLDListElement, MetricStripProps>(
  function MetricStrip({ className, items, metrics, ...props }, ref) {
    const styles = metricStripVariants();
    const resolved = resolveMetrics(metrics, items);

    return (
      <dl ref={ref} className={cn(styles.root(), className)} {...props}>
        {resolved.map((metric, index) => (
          <MetricTile key={metricKey(metric, index)} {...metric} />
        ))}
      </dl>
    );
  },
);
MetricStrip.displayName = "MetricStrip";

function resolveMetrics(
  metrics: readonly MetricTileValue[] | undefined,
  items: readonly (readonly [React.ReactNode, React.ReactNode])[] | undefined,
): readonly MetricTileValue[] {
  if (metrics) return metrics;
  return (
    items?.map(([label, value]) => ({
      label,
      value,
    })) ?? []
  );
}

function metricKey(metric: MetricTileValue, index: number): string {
  return `${String(metric.label)}:${String(metric.value)}:${index}`;
}

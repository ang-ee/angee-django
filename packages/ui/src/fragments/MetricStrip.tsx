import * as React from "react";

import { useInAppLink } from "../lib/in-app-link";
import { cn } from "../lib/cn";
import { type Tone } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";
import { Tag } from "../ui/badge";
import { Card } from "../ui/card";
import { IconTile } from "../ui/icon-tile";
import { SectionEyebrow } from "../ui/section-eyebrow";
import { textRoleVariants } from "../ui/text";

export interface MetricTileValue {
  detail?: React.ReactNode;
  icon?: React.ReactNode | string;
  label: React.ReactNode;
  value: React.ReactNode;
  /** Semantic tone for the prominent-density label. */
  tone?: Tone;
  /** When set, the tile is a link to this href (rendered as an `<a>`). */
  href?: string;
}

export const metricStripVariants = tv({
  slots: {
    root: "grid gap-3 sm:grid-cols-2",
    tile: "min-w-0 shadow-none",
    header: "flex min-w-0 items-center justify-between gap-2",
    value: "m-0 truncate text-fg",
    detail: cn(textRoleVariants({ role: "caption", truncate: true }), "m-0 mt-1"),
  },
  variants: {
    density: {
      compact: {
        root: "xl:grid-cols-4",
        tile: "px-3 py-2.5",
        value: "mt-1 text-13 font-medium",
      },
      prominent: {
        root: "lg:grid-cols-4",
        tile: "px-4 py-3",
        header: "mb-3",
        value: "text-2xl font-semibold tabular-nums",
      },
    },
    valueSize: {
      default: {
        value: "",
      },
      lg: {
        value: "text-xl font-semibold leading-6 tabular-nums",
      },
    },
  },
  defaultVariants: { density: "compact", valueSize: "default" },
});

type MetricStripRecipeProps = VariantProps<typeof metricStripVariants>;

export type MetricDensity = NonNullable<MetricStripRecipeProps["density"]>;

export type MetricTileProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className"
> &
  Pick<React.AnchorHTMLAttributes<HTMLAnchorElement>, "target" | "download" | "rel"> &
  MetricTileValue & {
    className?: string;
    density?: MetricDensity;
    /** Named value typography, independent of the tile's density. */
    valueSize?: MetricStripRecipeProps["valueSize"];
  };

export type MetricStripProps = Omit<
  React.HTMLAttributes<HTMLDListElement>,
  "className"
> & {
  className?: string;
  density?: MetricDensity;
  items?: readonly (readonly [React.ReactNode, React.ReactNode])[];
  metrics?: readonly MetricTileValue[];
};

const NAVIGABLE_TILE =
  "cursor-pointer no-underline outline-none transition hover:ring-2 hover:ring-border-focus focus-visible:focus-ring";

export const MetricTile = React.forwardRef<HTMLElement, MetricTileProps>(
  function MetricTile(
    {
      className,
      density = "compact",
      detail,
      icon,
      label,
      value,
      tone,
      href,
      onClick,
      valueSize = "default",
      ...props
    },
    ref,
  ) {
    const link = useInAppLink(href, { ...props, onClick });
    const styles = metricStripVariants({ density, valueSize });
    const body = (
      <>
        <div className={styles.header()}>
          {density === "prominent" ? (
            <dt className="contents">
              <Tag tone={tone ?? "neutral"}>{label}</Tag>
            </dt>
          ) : (
            <SectionEyebrow as="dt">{label}</SectionEyebrow>
          )}
          {icon ? <IconTile icon={icon} size="md" /> : null}
        </div>
        <dd className={styles.value()}>{value}</dd>
        {detail ? <p className={styles.detail()}>{detail}</p> : null}
      </>
    );

    if (href != null) {
      return (
        <Card asChild className={styles.tile({ className: cn(className, NAVIGABLE_TILE) })} density="sm">
          <a
            ref={ref as React.Ref<HTMLAnchorElement>}
            href={href}
            {...props}
            {...link}
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
  function MetricStrip({ className, density = "compact", items, metrics, ...props }, ref) {
    const styles = metricStripVariants({ density });
    const resolved = resolveMetrics(metrics, items);

    return (
      <dl ref={ref} className={cn(styles.root(), className)} {...props}>
        {resolved.map((metric, index) => (
          <MetricTile key={metricKey(metric, index)} density={density} {...metric} />
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

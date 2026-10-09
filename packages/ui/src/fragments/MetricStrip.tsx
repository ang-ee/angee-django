import * as React from "react";
import NumberFlow, { type Format, type NumberFlowProps } from "@number-flow/react";

import { useInAppLink } from "../lib/in-app-link";
import { cn } from "../lib/cn";
import { formatNumber } from "../lib/format-number";
import { getHumanLocale } from "../lib/human-locale";
import { type Tone } from "../lib/tones";
import { useMotionTokens } from "../lib/use-motion-tokens";
import { tv, type VariantProps } from "../lib/variants";
import { Tag } from "../ui/badge";
import { Card } from "../ui/card";
import { IconTile } from "../ui/icon-tile";
import {
  DefinitionPair,
  definitionFromTuple,
  type DefinitionPairTuple,
} from "./DefinitionPair";

export interface MetricTileValue {
  /** Opt into animated transitions for the typed numeric value. */
  animate?: boolean;
  detail?: React.ReactNode;
  /** Native Intl formatting options for the typed numeric value. */
  format?: Format;
  /** Stable identity for preserving a tile across label or value changes. */
  id?: string;
  icon?: React.ReactNode | string;
  label: React.ReactNode;
  /** Typed numeric path; when set, it takes precedence over the value slot. */
  numericValue?: number | bigint;
  /** Native NumberFlow suffix for the typed numeric value. */
  suffix?: string;
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
    value: "",
  },
  variants: {
    density: {
      compact: {
        root: "xl:grid-cols-4",
        tile: "px-3 py-2.5",
      },
      prominent: {
        root: "lg:grid-cols-4",
        tile: "px-4 py-3",
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
  items?: readonly DefinitionPairTuple[];
  metrics?: readonly MetricTileValue[];
};

export const MetricTile = React.forwardRef<HTMLElement, MetricTileProps>(
  function MetricTile(
    {
      animate = false,
      className,
      density = "compact",
      detail,
      format,
      icon,
      label,
      numericValue,
      suffix,
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
    const resolvedValue = numericValue == null
      ? value
      : animate
        ? <AnimatedMetricTileNumber format={format} suffix={suffix} value={numericValue} />
        : <>{formatNumber(numericValue, format)}{suffix}</>;
    const body = (
      <DefinitionPair
        action={icon ? <IconTile icon={icon} size="md" /> : undefined}
        density={density}
        detail={detail}
        label={
          density === "prominent" ? (
            <Tag tone={tone ?? "neutral"}>{label}</Tag>
          ) : (
            label
          )
        }
        orientation="stacked"
        value={
          valueSize === "lg" ? (
            <span className={styles.value()}>{resolvedValue}</span>
          ) : (
            resolvedValue
          )
        }
      />
    );

    if (href != null) {
      return (
        <Card asChild className={styles.tile({ className })} density="sm" interactive>
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

function AnimatedMetricTileNumber({
  format,
  suffix,
  value,
}: {
  format?: Format;
  suffix?: string;
  value: number | bigint;
}): React.ReactElement {
  const timing = useMotionTokens();
  // NumberFlow accepts precise numeric strings but excludes bigint from its
  // animation value type because transition direction is computed as a number.
  const numberFlowValue = (
    typeof value === "bigint" ? value.toString() : value
  ) as NumberFlowProps["value"];
  return (
    <NumberFlow
      animated
      format={format}
      locales={getHumanLocale()}
      opacityTiming={timing}
      respectMotionPreference
      spinTiming={timing}
      suffix={suffix}
      transformTiming={timing}
      value={numberFlowValue}
    />
  );
}

export const MetricStrip = React.forwardRef<HTMLDListElement, MetricStripProps>(
  function MetricStrip({ className, density = "compact", items, metrics, ...props }, ref) {
    const styles = metricStripVariants({ density });
    const resolved: readonly MetricTileValue[] =
      metrics ?? items?.map((item) => definitionFromTuple(item)) ?? [];

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

function metricKey(metric: MetricTileValue, index: number): string {
  return metric.id ?? `${String(metric.label)}:${index}`;
}

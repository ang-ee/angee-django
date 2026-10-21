import * as React from "react";
import NumberFlow, { type Format, type NumberFlowProps } from "@number-flow/react";

import { useInAppLink } from "../lib/in-app-link";
import { cn } from "../lib/cn";
import { formatNumber } from "../lib/format-number";
import { getHumanLocale } from "../lib/human-locale";
import { type Tone } from "../lib/tones";
import { useMotionTokens } from "../lib/use-motion-tokens";
import { tv, type VariantProps } from "../lib/variants";
import { Chip } from "../ui/chip";
import { Card } from "../ui/card";
import { IconTile } from "../ui/icon-tile";
import { Skeleton, SkeletonStatus } from "../ui/skeleton";
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
  /**
   * Typed numeric path; when set, it takes precedence over the value slot.
   * A decimal string formats and animates without a lossy Number conversion.
   */
  numericValue?: number | bigint | string;
  /** Native NumberFlow suffix for the typed numeric value. */
  suffix?: string;
  /** Rendered value slot; unused when `numericValue` is set. */
  value?: React.ReactNode;
  /** Semantic tone for the prominent-density label. */
  tone?: Tone;
  /** When set, the tile is a link to this href (rendered as an `<a>`). */
  href?: string;
  /** Controlled selection, for a metric that scopes the surrounding view. */
  selected?: boolean;
  /** An action metric renders a native button; navigation metrics use `href`. */
  onClick?: React.MouseEventHandler<HTMLElement>;
  disabled?: boolean;
}

/**
 * @kind token
 */
export const metricStripVariants = tv({
  slots: {
    root: "grid grid-cols-[repeat(auto-fit,minmax(min(100%,12rem),1fr))] gap-3",
    tile: "min-w-0 shadow-none",
    value: "",
  },
  variants: {
    density: {
      compact: {
        root: "",
        tile: "px-3 py-2.5",
      },
      prominent: {
        root: "gap-4",
        tile: "px-4 py-4",
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
  React.HTMLAttributes<HTMLDivElement>,
  "className"
> & {
  className?: string;
  density?: MetricDensity;
  items?: readonly DefinitionPairTuple[];
  metrics?: readonly MetricTileValue[];
  /** Renders skeleton tiles while the metrics load. */
  loading?: boolean;
  /** How many skeleton tiles a loading strip shows (default 4). */
  loadingCount?: number;
  loadingMessage?: string;
};

/**
 * @kind fragment
 */
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
      selected,
      disabled,
      valueSize = "default",
      ...props
    },
    ref,
  ) {
    const link = useInAppLink(href, {
      ...props,
      onClick: (event) => {
        if (disabled) event.preventDefault();
        else onClick?.(event);
      },
    });
    const styles = metricStripVariants({ density, valueSize });
    const resolvedValue = numericValue == null
      ? value
      : animate
        ? <AnimatedMetricTileNumber format={format} suffix={suffix} value={numericValue} />
        : <>{formatNumber(numericValue, format)}{suffix}</>;
    const body = (
      <dl>
        <DefinitionPair
          action={icon ? <IconTile icon={icon} size="md" /> : undefined}
          density={density}
          detail={detail}
          label={
            density === "prominent" ? (
              // The label sits as a grid item of the stacked pair: a pill must not stretch to the tile.
              <Chip tone={tone ?? "neutral"} className="justify-self-start">{label}</Chip>
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
      </dl>
    );

    if (href != null) {
      return (
        <Card asChild className={styles.tile({ className })} density="sm" interactive selected={selected}>
          <a
            ref={ref as React.Ref<HTMLAnchorElement>}
            href={disabled ? undefined : href}
            aria-current={selected ? "true" : undefined}
            aria-disabled={disabled || undefined}
            tabIndex={disabled ? -1 : undefined}
            {...props}
            {...link}
          >
            {body}
          </a>
        </Card>
      );
    }

    if (onClick) {
      return (
        <Card
          ref={ref}
          className={styles.tile({ className })}
          density="sm"
          interactive
          selected={selected}
          aria-pressed={selected}
          disabled={disabled}
          onClick={onClick}
          {...props}
        >
          {body}
        </Card>
      );
    }

    return (
      <Card asChild className={styles.tile({ className })} density="sm" selected={selected}>
        <div ref={ref as React.Ref<HTMLDivElement>} {...props}>
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
  value: number | bigint | string;
}): React.ReactElement {
  const timing = useMotionTokens();
  // NumberFlow formats through Intl, which accepts precise decimal strings;
  // its value type mirrors TypeScript's Intl declaration, which has not caught
  // up with that ES2023 contract, and excludes bigint because transition
  // direction is computed as a number.
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

/**
 * @kind fragment
 */
export const MetricStrip = React.forwardRef<HTMLDivElement, MetricStripProps>(
  function MetricStrip({ className, density = "compact", items, metrics, loading = false, loadingCount = 4, loadingMessage, ...props }, ref) {
    const styles = metricStripVariants({ density });
    const resolved: readonly MetricTileValue[] =
      metrics ?? items?.map((item) => definitionFromTuple(item)) ?? [];

    if (loading) {
      // A strip that announces itself is a status; one inside another skeleton status (a
      // detail surface) is plain hidden geometry, so the page keeps one live region.
      const tiles = Array.from({ length: loadingCount }, (_, index) => (
        <Card key={index} data-slot="metric-skeleton" className={styles.tile()} density="sm">
          <Skeleton shape="text" size="sm" className="w-16" />
          <Skeleton shape="text" size="lg" className="mt-3 w-24" />
        </Card>
      ));
      return loadingMessage
        ? <SkeletonStatus label={loadingMessage} className={cn(styles.root(), className)}>{tiles}</SkeletonStatus>
        : <div aria-hidden="true" className={cn(styles.root(), className)}>{tiles}</div>;
    }

    return (
      <div ref={ref} className={cn(styles.root(), className)} {...props}>
        {resolved.map((metric, index) => (
          <MetricTile key={metricKey(metric, index)} density={density} {...metric} />
        ))}
      </div>
    );
  },
);
MetricStrip.displayName = "MetricStrip";

function metricKey(metric: MetricTileValue, index: number): string {
  return metric.id ?? `${String(metric.label)}:${index}`;
}

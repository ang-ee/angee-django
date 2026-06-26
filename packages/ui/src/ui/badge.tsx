import * as React from "react";

import { cn } from "../lib/cn";
import { toneClass, toneFill, type Fill, type Tone } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";

export const badgeVariants = tv({
  base: "inline-flex min-w-0 items-center gap-1 whitespace-nowrap border font-medium leading-none",
  variants: {
    shape: {
      rounded: "rounded",
      pill: "rounded-full",
    },
    density: {
      default: "h-tag-h px-2 text-2xs",
      compact: "h-tag-h px-1.5 text-2xs",
      micro: "h-tag-h px-1 text-2xs",
      tiny: "h-tag-h px-1 text-2xs",
    },
    block: {
      true: "flex w-full justify-between truncate text-left",
      false: "",
    },
  },
  defaultVariants: {
    shape: "rounded",
    density: "default",
    block: false,
  },
});

// Count pills keep two neutral treatments tuned for counters (`neutral`/`muted`)
// and reuse the shared soft matrix for the palette tones — one source of truth.
const COUNT_BADGE_TONES = {
  neutral: "border-border bg-inset text-fg-muted",
  muted: "border-border-subtle bg-sheet text-fg-subtle",
  brand: toneFill.brand.soft,
  info: toneFill.info.soft,
  success: toneFill.success.soft,
  warning: toneFill.warning.soft,
  danger: toneFill.danger.soft,
} as const;

export const countBadgeVariants = tv({
  base: "inline-flex min-w-4 items-center justify-center rounded-full border px-1.5 text-2xs font-semibold leading-none tabular-nums",
  variants: {
    tone: COUNT_BADGE_TONES,
    size: {
      sm: "h-4",
      md: "h-tag-h",
    },
  },
  defaultVariants: {
    tone: "neutral",
    size: "sm",
  },
});

type BadgeRecipeProps = VariantProps<typeof badgeVariants>;
type CountBadgeRecipeProps = VariantProps<typeof countBadgeVariants>;

export type BadgeShape = NonNullable<BadgeRecipeProps["shape"]>;
export type BadgeDensity = NonNullable<BadgeRecipeProps["density"]>;
export type CountBadgeTone = NonNullable<CountBadgeRecipeProps["tone"]>;
export type CountBadgeSize = NonNullable<CountBadgeRecipeProps["size"]>;

export type BadgeProps = Omit<
  React.HTMLAttributes<HTMLSpanElement>,
  "className" | "color"
> &
  BadgeRecipeProps & {
    className?: string;
    tone?: Tone;
    variant?: Fill;
  };

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(function Badge(
  {
    tone = "neutral",
    variant = "soft",
    shape = "rounded",
    density = "default",
    block = false,
    className,
    children,
    ...props
  },
  ref,
) {
  return (
    <span
      ref={ref}
      className={cn(
        badgeVariants({ shape, density, block }),
        toneClass(tone, variant),
        className,
      )}
      {...props}
    >
      {children}
    </span>
  );
});

Badge.displayName = "Badge";

export type TagProps = BadgeProps;

export function Tag(props: TagProps) {
  return <Badge {...props} />;
}

export type CountBadgeProps = Omit<
  React.HTMLAttributes<HTMLSpanElement>,
  "className" | "color"
> &
  CountBadgeRecipeProps & {
    className?: string;
    max?: number;
    value?: number | string;
  };

function formatCount(value: number | string | undefined, max: number | undefined): string {
  if (value === undefined) return "";
  if (typeof value === "number") {
    if (max !== undefined && value > max) return `${max.toLocaleString()}+`;
    return value.toLocaleString();
  }
  return value;
}

export const CountBadge = React.forwardRef<HTMLSpanElement, CountBadgeProps>(
  function CountBadge(
    {
      tone = "neutral",
      size = "sm",
      className,
      children,
      value,
      max,
      ...props
    },
    ref,
  ) {
    return (
      <span
        ref={ref}
        className={cn(countBadgeVariants({ tone, size }), className)}
        {...props}
      >
        {children ?? formatCount(value, max)}
      </span>
    );
  },
);

CountBadge.displayName = "CountBadge";

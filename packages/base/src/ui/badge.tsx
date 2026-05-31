import * as React from "react";

import { cn } from "../lib/cn";
import { tones, type ToneName } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";

const tagVariantClasses: Record<ToneName, string> = {
  default: tones.default.badge,
  brand: tones.brand.badge,
  accent: tones.accent.badge,
  success: tones.success.badge,
  warning: tones.warning.badge,
  danger: tones.danger.badge,
  info: tones.info.badge,
  purple: tones.purple.badge,
  pink: tones.pink.badge,
};

export type TagVariant = ToneName;
export type BadgeVariant = TagVariant;

export const TAG_VARIANT_CLASSES: Record<TagVariant, string> = tagVariantClasses;

export const badgeVariants = tv({
  base: "inline-flex min-w-0 items-center gap-1 whitespace-nowrap font-medium leading-none",
  variants: {
    variant: TAG_VARIANT_CLASSES,
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
    variant: "default",
    shape: "rounded",
    density: "default",
    block: false,
  },
});

export const countBadgeVariants = tv({
  base: "inline-flex min-w-4 items-center justify-center rounded-full border px-1.5 text-2xs font-semibold leading-none tabular-nums",
  variants: {
    tone: {
      default: "border-border bg-inset text-fg-muted",
      muted: "border-border-subtle bg-sheet text-fg-subtle",
      brand: "border-brand-soft bg-brand-soft text-brand-soft-text",
      info: "border-info-soft bg-info-soft text-info-text",
      success: "border-success-soft bg-success-soft text-success-text",
      warning: "border-warning-soft bg-warning-soft text-warning-text",
      danger: "border-danger-soft bg-danger-soft text-danger-text",
    },
    size: {
      sm: "h-4",
      md: "h-tag-h",
    },
  },
  defaultVariants: {
    tone: "default",
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
  };

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(function Badge(
  {
    variant = "default",
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
      className={cn(badgeVariants({ variant, shape, density, block }), className)}
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
      tone = "default",
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

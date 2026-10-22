import * as React from "react";

import { cn } from "../lib/cn";
import { formatNumber } from "../lib/format-number";
import { toneClass, type Fill, type Tone } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";

/**
 * @kind token
 */
export const countBadgeVariants = tv({
  base: "inline-flex items-center justify-center rounded-full border font-semibold leading-none tabular-nums",
  variants: {
    size: {
      xs: "h-3.5 min-w-3.5 px-0.5 text-2xs",
      sm: "h-4 min-w-4 px-1.5 text-2xs",
      md: "h-tag-h min-w-4 px-1.5 text-2xs",
    },
    // An overlay count sits on the corner of its host (an icon button, an app tile).
    placement: {
      inline: "",
      overlay: "absolute -right-1 -top-1 ring-2 ring-sheet",
    },
  },
  defaultVariants: {
    size: "sm",
    placement: "inline",
  },
});

type CountBadgeRecipeProps = VariantProps<typeof countBadgeVariants>;

export type CountBadgeProps = Omit<
  React.HTMLAttributes<HTMLSpanElement>,
  "className" | "color"
> &
  CountBadgeRecipeProps & {
    className?: string;
    tone?: Tone;
    variant?: Fill;
    /** Counts above `max` read as "max+". */
    max?: number;
    value?: number | string;
  };

/** The one numeric count pill: tab counts, pane icon-button counts, app-tile counts, tree counts.
 * @kind primitive
 */
export const CountBadge = React.forwardRef<HTMLSpanElement, CountBadgeProps>(
  function CountBadge(
    { tone = "neutral", variant = "soft", size = "sm", placement = "inline", className, children, value, max, ...props },
    ref,
  ) {
    return (
      <span
        ref={ref}
        className={cn(countBadgeVariants({ size, placement }), toneClass(tone, variant), className)}
        {...props}
      >
        {children ?? formatCount(value, max)}
      </span>
    );
  },
);
CountBadge.displayName = "CountBadge";

function formatCount(value: number | string | undefined, max: number | undefined): string {
  if (value === undefined) return "";
  if (typeof value !== "number") return value;
  return max !== undefined && value > max ? `${formatNumber(max)}+` : formatNumber(value);
}

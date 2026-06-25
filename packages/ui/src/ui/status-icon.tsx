import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import type { IconComponent } from "../chrome/icon-registry";
import { INTENT_GLYPHS, TONES, toneSolidBg, type FeedbackIntent, type Tone } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";

/** A tinted status glyph speaks in feedback tones (+ a `muted` quiet state). */
export type StatusIconTone = FeedbackIntent | "muted";

const STATUS_ICON_TONES: Record<StatusIconTone, string> = {
  info: "text-info-text",
  success: "text-success-text",
  warning: "text-warning-text",
  danger: "text-danger-text",
  muted: "text-fg-muted",
};

export const statusIconVariants = tv({
  base: "inline-flex shrink-0 items-center justify-center rounded-full [&_.glyph]:shrink-0 [&>svg]:shrink-0",
  variants: {
    tone: STATUS_ICON_TONES,
    size: {
      sm: "size-4 [&_.glyph]:size-4 [&>svg]:size-4",
      md: "size-5 [&_.glyph]:size-5 [&>svg]:size-5",
      lg: "size-6 [&_.glyph]:size-6 [&>svg]:size-6",
    },
  },
  defaultVariants: {
    tone: "info",
    size: "sm",
  },
});

// Dots are a solid mark, so they take the palette's solid fill bg (via the
// shared `toneSolidBg` owner). Neutral keeps the muted gray — a near-black solid
// would read as a bullet, not a status.
const STATUS_DOT_TONES = Object.fromEntries(
  TONES.map((tone) => [tone, tone === "neutral" ? "bg-fg-muted" : toneSolidBg(tone)]),
) as Record<Tone, string>;

export const statusDotVariants = tv({
  base: "inline-flex shrink-0 rounded-full",
  variants: {
    tone: STATUS_DOT_TONES,
    size: {
      sm: "size-1.5",
      md: "size-2",
      lg: "size-2.5",
    },
  },
  defaultVariants: {
    tone: "neutral",
    size: "md",
  },
});

type StatusIconRecipeProps = VariantProps<typeof statusIconVariants>;
type StatusDotRecipeProps = VariantProps<typeof statusDotVariants>;

export type StatusIconSize = NonNullable<StatusIconRecipeProps["size"]>;
export type StatusDotTone = NonNullable<StatusDotRecipeProps["tone"]>;
export type StatusDotSize = NonNullable<StatusDotRecipeProps["size"]>;

export type StatusIconProps = Omit<
  React.HTMLAttributes<HTMLSpanElement>,
  "className" | "color"
> &
  StatusIconRecipeProps & {
    className?: string;
    icon?: IconComponent | string;
    label?: string;
  };

export const StatusIcon = React.forwardRef<HTMLSpanElement, StatusIconProps>(
  function StatusIcon(
    { className, icon, tone = "info", label, size = "sm", ...props },
    ref,
  ) {
    const Icon = typeof icon === "function" ? icon : undefined;
    const iconName =
      typeof icon === "string"
        ? icon
        : tone === "muted"
          ? "help"
          : INTENT_GLYPHS[tone];

    return (
      <span
        ref={ref}
        aria-hidden={label ? undefined : true}
        aria-label={label}
        className={statusIconVariants({ className, tone, size })}
        role={label ? "img" : undefined}
        {...props}
      >
        {Icon ? (
          <Icon aria-hidden="true" focusable="false" strokeWidth={2.25} />
        ) : (
          <Glyph decorative name={iconName} size="1em" strokeWidth={2.25} />
        )}
      </span>
    );
  },
);
StatusIcon.displayName = "StatusIcon";

export type StatusDotProps = Omit<
  React.HTMLAttributes<HTMLSpanElement>,
  "className" | "color"
> &
  StatusDotRecipeProps & {
    className?: string;
    label?: string;
    tone?: Tone;
  };

export const StatusDot = React.forwardRef<HTMLSpanElement, StatusDotProps>(
  function StatusDot({ className, label, size = "md", tone = "neutral", ...props }, ref) {
    return (
      <span
        ref={ref}
        aria-hidden={label ? undefined : true}
        aria-label={label}
        className={statusDotVariants({ className, size, tone })}
        role={label ? "img" : undefined}
        {...props}
      />
    );
  },
);
StatusDot.displayName = "StatusDot";

import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import type { IconComponent } from "../chrome/icon-registry";
import { INTENT_GLYPHS, tones, type ToneName } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";

const statusIntentClasses = {
  info: tones.info.dotFg,
  success: tones.success.dotFg,
  warning: tones.warning.dotFg,
  danger: tones.danger.dotFg,
  muted: tones.default.dotFg,
};

export const statusIconVariants = tv({
  base: "inline-flex shrink-0 items-center justify-center rounded-full [&_.glyph]:shrink-0 [&>svg]:shrink-0",
  variants: {
    intent: statusIntentClasses,
    size: {
      sm: "size-4 [&_.glyph]:size-4 [&>svg]:size-4",
      md: "size-5 [&_.glyph]:size-5 [&>svg]:size-5",
      lg: "size-6 [&_.glyph]:size-6 [&>svg]:size-6",
    },
  },
  defaultVariants: {
    intent: "info",
    size: "sm",
  },
});

export const statusDotVariants = tv({
  base: "inline-flex shrink-0 rounded-full",
  variants: {
    tone: {
      default: tones.default.barFg,
      brand: tones.brand.barFg,
      accent: tones.accent.barFg,
      success: tones.success.barFg,
      warning: tones.warning.barFg,
      danger: tones.danger.barFg,
      info: tones.info.barFg,
      purple: tones.purple.barFg,
      pink: tones.pink.barFg,
    },
    size: {
      sm: "size-1.5",
      md: "size-2",
      lg: "size-2.5",
    },
  },
  defaultVariants: {
    tone: "default",
    size: "md",
  },
});

type StatusIconRecipeProps = VariantProps<typeof statusIconVariants>;
type StatusDotRecipeProps = VariantProps<typeof statusDotVariants>;

export type StatusIconIntent = NonNullable<StatusIconRecipeProps["intent"]>;
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
    { className, icon, intent = "info", label, size = "sm", ...props },
    ref,
  ) {
    const Icon = typeof icon === "function" ? icon : undefined;
    const iconName =
      typeof icon === "string"
        ? icon
        : intent === "muted"
          ? "help"
          : INTENT_GLYPHS[intent];

    return (
      <span
        ref={ref}
        aria-hidden={label ? undefined : true}
        aria-label={label}
        className={statusIconVariants({ className, intent, size })}
        role={label ? "img" : undefined}
        {...props}
      >
        {Icon ? (
          <Icon aria-hidden="true" focusable="false" strokeWidth={2.25} />
        ) : (
          <Glyph
            decorative
            name={iconName}
            size="1em"
            className="[&_*]:stroke-[2.25]"
          />
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
    tone?: ToneName;
  };

export const StatusDot = React.forwardRef<HTMLSpanElement, StatusDotProps>(
  function StatusDot({ className, label, size = "md", tone = "default", ...props }, ref) {
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

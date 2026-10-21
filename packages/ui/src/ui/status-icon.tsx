import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import type { IconComponent } from "../chrome/icon-registry";
import {
  FEEDBACK_INTENTS,
  INTENT_GLYPHS,
  TONES,
  toneSolidBg,
  toneText,
  type FeedbackIntent,
  type Tone,
} from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";
import { cn } from "../lib/cn";
import { Button } from "./button";
import { Tooltip } from "./tooltip";

/** A tinted status glyph speaks in feedback tones (+ the `muted` quiet tone). */
export type StatusIconTone = FeedbackIntent | "muted";

const STATUS_ICON_TONE_CLASSES = Object.fromEntries(
  [...FEEDBACK_INTENTS, "muted" as const].map((tone) => [tone, toneText(tone)] as const),
) as Record<StatusIconTone, string>;

/**
 * @kind token
 */
export const statusIconVariants = tv({
  base: "inline-flex shrink-0 items-center justify-center rounded-full [&_.glyph]:shrink-0 [&>svg]:shrink-0",
  variants: {
    tone: STATUS_ICON_TONE_CLASSES,
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
  TONES.map((tone) => [tone, tone === "neutral" ? "bg-fg-muted" : tone === "muted" ? "bg-fg-subtle" : toneSolidBg(tone)]),
) as Record<Tone, string>;

/**
 * @kind token
 */
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

export type StatusIconProps = Omit<
  React.HTMLAttributes<HTMLSpanElement>,
  "className" | "color"
> &
  StatusIconRecipeProps & {
    className?: string;
    icon?: IconComponent | string;
    label?: string;
  };

/**
 * @kind primitive
 */
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

export interface StatusIconItem {
  key: string;
  /** The complete accessible meaning, including the status, not just the subject. */
  label: string;
  description?: string;
  /** Supplied by the status owner; the group never infers a result from missing items. */
  tone: StatusIconTone;
  icon?: StatusIconProps["icon"];
}

export interface StatusIconGroupProps {
  label: string;
  items: readonly StatusIconItem[];
  className?: string;
  /** Makes each status a keyboard-accessible action, such as opening its evidence. */
  onSelect?: (key: string) => void;
}

/** A compact row of independently labelled outcomes; colors always accompany glyphs and text.
 * @owner StatusIcon
 */
export function StatusIconGroup({ label, items, className, onSelect }: StatusIconGroupProps): React.ReactElement {
  return (
    <span role="group" aria-label={label} className={cn("inline-flex flex-wrap items-center gap-1", className)}>
      {items.map((item) => (
        <Tooltip key={item.key} label={
          <span className="grid gap-1">
            <span>{item.label}</span>
            {item.description ? <span className="font-normal">{item.description}</span> : null}
          </span>
        }>
          {onSelect ? (
            <Button variant="ghost" size="iconSm" aria-label={item.label} onClick={() => onSelect(item.key)}>
              <StatusIcon tone={item.tone} icon={item.icon} />
            </Button>
          ) : (
            <StatusIcon
              tone={item.tone}
              icon={item.icon}
              label={item.label}
              tabIndex={0}
              className="m-1 outline-none focus-visible:focus-ring"
            />
          )}
        </Tooltip>
      ))}
    </span>
  );
}

export type StatusDotProps = Omit<
  React.HTMLAttributes<HTMLSpanElement>,
  "className" | "color"
> &
  StatusDotRecipeProps & {
    className?: string;
    label?: string;
    tone?: Tone;
  };

/**
 * @kind primitive
 */
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

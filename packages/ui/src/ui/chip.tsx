import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { toneClass, type Fill, type Tone } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";
import { Button } from "./button";

export const chipVariants = tv({
  base: "inline-flex max-w-full shrink-0 items-center gap-1 truncate whitespace-nowrap border font-medium leading-none",
  variants: {
    shape: {
      rounded: "rounded-6",
      pill: "rounded-full",
    },
    size: {
      micro: "h-tag-h px-1.5 text-2xs",
      sm: "h-5 px-2 text-2xs",
      md: "h-6 px-2.5 text-13",
    },
    mono: {
      true: "font-mono",
      false: "",
    },
  },
  defaultVariants: {
    shape: "pill",
    size: "micro",
    mono: false,
  },
});

export type ChipRecipeProps = VariantProps<typeof chipVariants>;

export type ChipShape = NonNullable<ChipRecipeProps["shape"]>;
export type ChipSize = NonNullable<ChipRecipeProps["size"]>;

/** Chip-local tones outside the semantic palette: `muted` (quiet) and `inherit`
 *  (adopts the surrounding text color). Palette tones route through the matrix. */
export type ChipTone = Tone | "muted" | "inherit";

const CHIP_LOCAL_TONES = {
  muted: "border-transparent bg-sheet text-fg-muted",
  inherit: "border-current/20 bg-transparent text-current",
} as const;

function chipToneClass(tone: ChipTone, variant: Fill): string {
  if (tone === "muted" || tone === "inherit") return CHIP_LOCAL_TONES[tone];
  return toneClass(tone, variant);
}

export type ChipProps = Omit<
  React.HTMLAttributes<HTMLSpanElement>,
  "className" | "color"
> &
  ChipRecipeProps & {
    className?: string;
    tone?: ChipTone;
    variant?: Fill;
  };

export const Chip = React.forwardRef<HTMLSpanElement, ChipProps>(
  function Chip(
    {
      className,
      mono = false,
      shape = "pill",
      size = "micro",
      tone = "neutral",
      variant = "soft",
      ...props
    },
    ref,
  ) {
    return (
      <span
        ref={ref}
        className={cn(
          chipVariants({ mono, shape, size }),
          chipToneClass(tone, variant),
          className,
        )}
        {...props}
      />
    );
  },
);
Chip.displayName = "Chip";

export type RemovableChipProps = ChipProps & {
  /**
   * Subject named in the remove button's aria label. The button copy
   * ("Remove {label}") is owned here through `useUiT`, so callers pass only the
   * thing being removed (a tag, a related record) — never a pre-built sentence.
   */
  removeLabel: string;
  onRemove: () => void;
};

/**
 * A `Chip` carrying a trailing remove button — the single owner for the removable
 * chips widgets and pages hand-rolled (many2many, tagInput, facet filters). The
 * body truncates; the remove affordance and its localized aria copy live here once.
 */
export const RemovableChip = React.forwardRef<HTMLSpanElement, RemovableChipProps>(
  function RemovableChip(
    { children, className, onRemove, removeLabel, ...props },
    ref,
  ) {
    const t = useUiT();
    return (
      <Chip ref={ref} className={cn("gap-1 pr-1", className)} {...props}>
        <span className="min-w-0 truncate">{children}</span>
        <Button
          type="button"
          variant="ghost"
          size="iconSm"
          className="size-4 rounded-full"
          aria-label={t("chip.remove", { label: removeLabel })}
          onClick={onRemove}
        >
          <Glyph name="x" />
        </Button>
      </Chip>
    );
  },
);
RemovableChip.displayName = "RemovableChip";

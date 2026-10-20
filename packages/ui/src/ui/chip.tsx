import * as React from "react";

import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { hexColor, readableInk } from "../lib/hex-color";
import { isFeedbackIntent, toneClass, type Fill, type Tone } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";
import { Button } from "./button";
import { StatusDot, StatusIcon } from "./status-icon";
import { interactiveSurface } from "./widget-control";

/**
 * @kind token
 */
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
    // A dot mark turns the pill into a plain status row: the dot carries the tone.
    mark: {
      none: "",
      dot: "h-auto border-transparent bg-transparent px-0 text-inherit",
      icon: "",
    },
    mono: {
      true: "font-mono",
      false: "",
    },
    // The toggle mode: a pressed chip wears the shared selected surface.
    interactive: {
      true: cn(interactiveSurface({ focus: "visible", disabled: "pseudo" }), "cursor-pointer hover:bg-inset hover:text-fg aria-pressed:selected aria-pressed:border-brand aria-pressed:hover:selected"),
      false: "",
    },
  },
  defaultVariants: {
    shape: "pill",
    size: "micro",
    mark: "none",
    mono: false,
    interactive: false,
  },
});

export type ChipRecipeProps = VariantProps<typeof chipVariants>;

export type ChipShape = NonNullable<ChipRecipeProps["shape"]>;
export type ChipSize = NonNullable<ChipRecipeProps["size"]>;
export type ChipMark = NonNullable<ChipRecipeProps["mark"]>;

/** The palette tones plus `inherit`, which adopts the surrounding text colour. */
export type ChipTone = Tone | "inherit";

const INHERIT_TONE = "border-current/20 bg-transparent text-current";

function chipToneClass(tone: ChipTone, variant: Fill): string {
  return tone === "inherit" ? INHERIT_TONE : toneClass(tone, variant);
}

export type ChipProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className" | "color"
> &
  Omit<ChipRecipeProps, "interactive"> & {
    className?: string;
    tone?: ChipTone;
    variant?: Fill;
    /** A record's own `#rrggbb` colour as the fill, with readable ink; replaces the tone. */
    color?: string;
    /**
     * The toggle mode: renders a `<button aria-pressed>` (a scope chip, a reaction).
     * A chip with `onClick` alone is a button too; without either it is a span.
     */
    pressed?: boolean;
    disabled?: boolean;
    title?: string;
  };

/**
 * The one pill: tone × fill × size × shape, with a status mark and a toggle mode.
 * @kind primitive
 */
export const Chip = React.forwardRef<HTMLElement, ChipProps>(
  function Chip(
    {
      className,
      mono = false,
      shape = "pill",
      size = "micro",
      mark = "none",
      tone = "neutral",
      variant = "soft",
      color,
      pressed,
      disabled,
      style,
      children,
      onClick,
      ...props
    },
    ref,
  ) {
    const fill = hexColor(color);
    const interactive = pressed !== undefined || onClick !== undefined;
    const classes = cn(
      chipVariants({ mono, shape, size, mark, interactive }),
      fill || mark === "dot" ? undefined : chipToneClass(tone, variant),
      className,
    );
    const inlineStyle = fill ? { backgroundColor: fill, color: readableInk(fill), ...style } : style;
    const content = (
      <>
        {mark === "dot" && tone !== "inherit" ? <StatusDot tone={tone} size="md" /> : null}
        {mark === "icon" && isFeedbackIntent(tone) ? <StatusIcon tone={tone} size="sm" /> : null}
        {children}
      </>
    );
    if (interactive) {
      return (
        <button
          ref={ref as React.Ref<HTMLButtonElement>}
          type="button"
          aria-pressed={pressed}
          disabled={disabled}
          className={classes}
          style={inlineStyle}
          onClick={onClick}
          {...(props as React.ButtonHTMLAttributes<HTMLButtonElement>)}
        >
          {content}
        </button>
      );
    }
    return (
      <span ref={ref as React.Ref<HTMLSpanElement>} className={classes} style={inlineStyle} {...props}>
        {content}
      </span>
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
 * chips widgets and pages hand-rolled (value lists, facet filters). The body
 * truncates; the remove affordance and its localized aria copy live here once.
 * @kind primitive
 */
export const RemovableChip = React.forwardRef<HTMLElement, RemovableChipProps>(
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
          // The chip's ink, so the remove mark reads on a record's own colour too.
          className="size-4 rounded-full text-current"
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

export interface ChipListItem {
  /** Unique within the list: the chip's key and the value `onRemove` reports. */
  id: string;
  label: React.ReactNode;
  /** Plain-text subject of the remove button when `label` is not text; defaults to `id`. */
  text?: string;
  /** The item's own `#rrggbb` colour (a related record's `color`), filling its chip. */
  color?: string;
}

export interface ChipListProps {
  items: readonly ChipListItem[];
  /** Makes every chip removable, reporting the removed item's `id`. */
  onRemove?: (id: string) => void;
  className?: string;
}

/**
 * A field value's items as one wrapping row of chips — the single owner for the
 * value lists widgets and cells render (related records, tags, string arrays).
 * @owner Chip
 */
export function ChipList({ items, onRemove, className }: ChipListProps): React.ReactElement {
  return (
    <span className={cn("inline-flex min-w-0 flex-wrap items-center gap-1", className)}>
      {items.map((item) => onRemove ? (
        <RemovableChip key={item.id} tone="info" size="sm" color={item.color} removeLabel={item.text ?? item.id}
          onRemove={() => onRemove(item.id)}>
          {item.label}
        </RemovableChip>
      ) : (
        <Chip key={item.id} tone="info" size="sm" color={item.color}>{item.label}</Chip>
      ))}
    </span>
  );
}

import * as React from "react";

import { tv, type VariantProps } from "../lib/variants";

export const chipVariants = tv({
  base: "inline-flex max-w-full shrink-0 items-center gap-1 truncate whitespace-nowrap border font-medium leading-none",
  variants: {
    tone: {
      default: "border-transparent bg-inset text-fg-2",
      muted: "border-transparent bg-sheet text-fg-muted",
      inherit: "border-current/20 bg-transparent text-current",
      brand: "border-transparent bg-brand-soft text-brand-soft-text",
      info: "border-transparent bg-info-soft text-info-text",
      success: "border-transparent bg-success-soft text-success-text",
      warning: "border-transparent bg-warning-soft text-warning-text",
      danger: "border-transparent bg-danger-soft text-danger-text",
    },
    shape: {
      rounded: "rounded",
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
    outline: {
      true: "border-current/20",
      false: "",
    },
  },
  defaultVariants: {
    tone: "default",
    shape: "pill",
    size: "micro",
    mono: false,
    outline: false,
  },
});

export type ChipRecipeProps = VariantProps<typeof chipVariants>;

export type ChipTone = NonNullable<ChipRecipeProps["tone"]>;
export type ChipShape = NonNullable<ChipRecipeProps["shape"]>;
export type ChipSize = NonNullable<ChipRecipeProps["size"]>;

export type ChipProps = Omit<
  React.HTMLAttributes<HTMLSpanElement>,
  "className" | "color"
> &
  ChipRecipeProps & {
    className?: string;
  };

export const Chip = React.forwardRef<HTMLSpanElement, ChipProps>(
  function Chip(
    {
      className,
      mono = false,
      outline = false,
      shape = "pill",
      size = "micro",
      tone = "default",
      ...props
    },
    ref,
  ) {
    return (
      <span
        ref={ref}
        className={chipVariants({
          className,
          mono,
          outline,
          shape,
          size,
          tone,
        })}
        {...props}
      />
    );
  },
);
Chip.displayName = "Chip";

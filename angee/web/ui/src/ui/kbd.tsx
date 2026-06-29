import * as React from "react";

import { tv, type VariantProps } from "../lib/variants";

export const kbdVariants = tv({
  base: "inline-flex items-center justify-center border border-b-2 font-mono font-medium leading-none",
  variants: {
    size: {
      sm: "h-4 min-w-4 rounded-4 px-1 text-2xs",
      md: "h-kbd-h min-w-kbd-h rounded-6 px-1.5 text-2xs",
      lg: "h-btn-sm min-w-btn-sm rounded-6 px-2 text-xs",
    },
    tone: {
      default: "border-border-subtle bg-inset text-fg-muted",
      subtle: "border-border bg-sheet text-fg-2",
      rail: "border-kbd-on-rail-border bg-kbd-on-rail text-on-rail-mut",
    },
  },
  defaultVariants: {
    size: "md",
    tone: "default",
  },
});

type KbdRecipeProps = VariantProps<typeof kbdVariants>;

export type KbdSize = NonNullable<KbdRecipeProps["size"]>;
export type KbdTone = NonNullable<KbdRecipeProps["tone"]>;

export type KbdProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className" | "color"
> &
  KbdRecipeProps & {
    className?: string;
  };

export const Kbd = React.forwardRef<HTMLElement, KbdProps>(function Kbd(
  { className, size = "md", tone = "default", ...props },
  ref,
) {
  return (
    <kbd ref={ref} className={kbdVariants({ className, size, tone })} {...props} />
  );
});
Kbd.displayName = "Kbd";

import * as React from "react";
import {
  Separator as BaseSeparator,
  type SeparatorProps as BaseSeparatorProps,
} from "@base-ui/react/separator";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";

/**
 * @kind token
 */
export const separatorVariants = tv({
  base: "shrink-0 bg-border",
  variants: {
    orientation: {
      horizontal: "h-px w-full",
      vertical: "h-full w-px",
    },
    tone: {
      default: "bg-border",
      subtle: "bg-border-subtle",
      strong: "bg-border-strong",
    },
  },
  defaultVariants: {
    orientation: "horizontal",
    tone: "default",
  },
});

type SeparatorRecipeProps = VariantProps<typeof separatorVariants>;

export type SeparatorProps = Omit<
  BaseSeparatorProps,
  "className" | "orientation"
> &
  SeparatorRecipeProps & {
    className?: string;
  };

/**
 * @kind primitive
 */
export const Separator = React.forwardRef<HTMLDivElement, SeparatorProps>(
  function Separator(
    {
      orientation = "horizontal",
      tone = "default",
      className,
      ...props
    },
    ref,
  ) {
    return (
      <BaseSeparator
        ref={ref}
        orientation={orientation}
        className={cn(separatorVariants({ orientation, tone }), className)}
        {...props}
      />
    );
  },
);

Separator.displayName = "Separator";


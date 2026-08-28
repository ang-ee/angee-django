import * as React from "react";
import {
  Separator as BaseSeparator,
  type SeparatorProps as BaseSeparatorProps,
} from "@base-ui/react/separator";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";

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

export type SeparatorOrientation = NonNullable<
  SeparatorRecipeProps["orientation"]
>;
export type SeparatorTone = NonNullable<SeparatorRecipeProps["tone"]>;

export type SeparatorProps = Omit<
  BaseSeparatorProps,
  "className" | "orientation"
> &
  SeparatorRecipeProps & {
    className?: string;
  };

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

export type DividerProps = Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "className" | "color"
> &
  Pick<SeparatorProps, "orientation" | "tone"> & {
    label?: React.ReactNode;
    className?: string;
  };

export function Divider({
  label,
  className,
  orientation = "horizontal",
  tone = "default",
  ...props
}: DividerProps): React.ReactElement {
  if (label === undefined || label === null) {
    return (
      <Separator
        className={className}
        orientation={orientation}
        tone={tone}
        {...props}
      />
    );
  }

  return (
    <div
      role="separator"
      aria-orientation={orientation}
      className={cn(
        "flex items-center gap-3 text-2xs uppercase tracking-wider text-fg-muted",
        orientation === "vertical" && "h-full flex-col",
        className,
      )}
      {...props}
    >
      <Separator
        aria-hidden="true"
        className="flex-1"
        orientation={orientation}
        tone={tone}
      />
      <span>{label}</span>
      <Separator
        aria-hidden="true"
        className="flex-1"
        orientation={orientation}
        tone={tone}
      />
    </div>
  );
}

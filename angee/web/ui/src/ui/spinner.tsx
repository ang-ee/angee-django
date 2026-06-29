import * as React from "react";

import { tv, type VariantProps } from "../lib/variants";

export const spinnerVariants = tv({
  base: "inline-block shrink-0 rounded-full border-2 border-current border-r-transparent align-middle motion-safe:animate-spin",
  variants: {
    size: {
      sm: "size-3",
      md: "size-4",
      lg: "size-5",
    },
    tone: {
      current: "text-current",
      muted: "text-fg-muted",
      brand: "text-brand",
      inverse: "text-on-brand",
    },
  },
  defaultVariants: {
    size: "sm",
    tone: "current",
  },
});

type SpinnerRecipeProps = VariantProps<typeof spinnerVariants>;

export type SpinnerSize = NonNullable<SpinnerRecipeProps["size"]>;
export type SpinnerTone = NonNullable<SpinnerRecipeProps["tone"]>;

export type SpinnerProps = Omit<
  React.HTMLAttributes<HTMLSpanElement>,
  "children"
> &
  SpinnerRecipeProps & {
    label?: string;
  };

export const Spinner = React.forwardRef<HTMLSpanElement, SpinnerProps>(
  function Spinner(
    { className, label, size = "sm", tone = "current", ...props },
    ref,
  ) {
    return (
      <span
        ref={ref}
        aria-hidden={label ? undefined : true}
        aria-label={label}
        className={spinnerVariants({ className, size, tone })}
        role={label ? "status" : undefined}
        {...props}
      />
    );
  },
);
Spinner.displayName = "Spinner";

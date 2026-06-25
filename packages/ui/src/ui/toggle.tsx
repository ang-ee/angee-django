import * as React from "react";
import {
  Toggle as BaseToggle,
  type ToggleProps as BaseToggleProps,
} from "@base-ui/react/toggle";

import { tv, type VariantProps } from "../lib/variants";

export const toggleVariants = tv({
  base: "inline-flex shrink-0 cursor-pointer select-none items-center justify-center gap-1.5 rounded text-13 font-medium outline-none transition-colors focus-visible:focus-ring disabled:cursor-not-allowed disabled:opacity-60 data-[pressed]:text-fg [&_.glyph]:size-3.5",
  variants: {
    variant: {
      default:
        "bg-transparent text-fg-muted hover:bg-inset hover:text-fg data-[pressed]:bg-brand-soft data-[pressed]:text-brand-soft-text",
      ghost:
        "bg-transparent text-fg-muted hover:bg-inset hover:text-fg data-[pressed]:bg-inset data-[pressed]:text-fg",
      outline:
        "border border-border bg-sheet text-fg hover:border-border-strong hover:bg-inset data-[pressed]:border-brand data-[pressed]:bg-brand-soft data-[pressed]:text-brand-soft-text",
    },
    size: {
      sm: "h-7 px-2",
      md: "h-8 px-2.5",
      // Icon-only sizes follow the Button convention (`iconSm`/`iconMd`).
      iconSm: "size-icon-btn-sm px-0",
      iconMd: "size-icon-btn-md px-0",
    },
  },
  defaultVariants: {
    variant: "default",
    size: "md",
  },
});

type ToggleRecipeProps = VariantProps<typeof toggleVariants>;

export type ToggleVariant = NonNullable<ToggleRecipeProps["variant"]>;
export type ToggleSize = NonNullable<ToggleRecipeProps["size"]>;

export type ToggleProps = Omit<BaseToggleProps<string>, "className"> &
  ToggleRecipeProps & {
    className?: string;
  };

export const Toggle = React.forwardRef<HTMLButtonElement, ToggleProps>(
  function Toggle(
    { className, variant = "default", size = "md", type = "button", ...props },
    ref,
  ) {
    return (
      <BaseToggle
        ref={ref}
        type={type}
        className={toggleVariants({ className, variant, size })}
        {...props}
      />
    );
  },
);
Toggle.displayName = "Toggle";

export const ToggleRoot = Toggle;

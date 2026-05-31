import * as React from "react";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { WIDGET_CONTROL_READONLY_CLASS } from "./widget-control";

export const textareaVariants = tv({
  base: "w-full rounded-md border border-border bg-sheet text-fg outline-none transition-colors placeholder:text-fg-subtle focus:border-border-focus focus:focus-ring disabled:cursor-not-allowed disabled:bg-inset disabled:opacity-60",
  variants: {
    size: {
      sm: "px-2 py-1 text-xs leading-snug",
      md: "px-2 py-1.5 text-13 leading-snug",
      lg: "px-3 py-2 text-sm leading-snug",
    },
    resize: {
      none: "resize-none",
      vertical: "resize-y",
      both: "resize",
    },
    invalid: {
      true: "border-danger focus:border-danger focus:focus-ring-danger",
      false: "",
    },
    readOnly: {
      true: WIDGET_CONTROL_READONLY_CLASS,
      false: "",
    },
  },
  defaultVariants: {
    size: "md",
    resize: "vertical",
    invalid: false,
    readOnly: false,
  },
});

type TextareaRecipeProps = VariantProps<typeof textareaVariants>;

export type TextareaSize = NonNullable<TextareaRecipeProps["size"]>;
export type TextareaResize = NonNullable<TextareaRecipeProps["resize"]>;

export type TextareaProps = Omit<
  React.TextareaHTMLAttributes<HTMLTextAreaElement>,
  "className" | "color"
> &
  TextareaRecipeProps & {
    className?: string;
  };

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea(
    {
      size = "md",
      resize = "vertical",
      invalid = false,
      readOnly = false,
      className,
      ...props
    },
    ref,
  ) {
    return (
      <textarea
        ref={ref}
        readOnly={readOnly}
        aria-invalid={invalid || undefined}
        className={cn(textareaVariants({ size, resize, invalid, readOnly }), className)}
        {...props}
      />
    );
  },
);

Textarea.displayName = "Textarea";

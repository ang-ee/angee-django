import * as React from "react";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";
import { widgetControlSurfaceVariants } from "./widget-control";

export const textareaVariants = tv({
  extend: widgetControlSurfaceVariants,
  base: "w-full text-fg placeholder:text-fg-subtle",
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
  },
  defaultVariants: {
    size: "md",
    resize: "vertical",
  },
});

type TextareaRecipeProps = VariantProps<typeof textareaVariants>;

export type TextareaSize = NonNullable<TextareaRecipeProps["size"]>;
export type TextareaResize = NonNullable<TextareaRecipeProps["resize"]>;

export type TextareaProps = Omit<
  React.TextareaHTMLAttributes<HTMLTextAreaElement>,
  "className" | "color"
> &
  Pick<TextareaRecipeProps, "size" | "resize" | "invalid" | "readOnly"> & {
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

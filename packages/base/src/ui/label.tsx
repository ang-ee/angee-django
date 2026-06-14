import * as React from "react";

import { cn } from "../lib/cn";
import { tv, type VariantProps } from "../lib/variants";

export const labelVariants = tv({
  base: "inline-flex min-w-0 items-center gap-1",
  variants: {
    variant: {
      standard: "text-13 normal-case tracking-normal",
      eyebrow: "text-2xs uppercase tracking-wide",
    },
    size: {
      sm: "text-xs",
      md: "",
      lg: "text-sm",
    },
    tone: {
      default: "text-fg",
      muted: "text-fg-muted",
      danger: "text-danger-text",
    },
    weight: {
      regular: "font-normal",
      medium: "font-medium",
      semibold: "font-semibold",
    },
    truncate: {
      true: "truncate",
      false: "",
    },
  },
  defaultVariants: {
    variant: "standard",
    size: "md",
    tone: "default",
    weight: "medium",
    truncate: false,
  },
});

export type LabelRecipeProps = VariantProps<typeof labelVariants>;
export type LabelVariant = NonNullable<LabelRecipeProps["variant"]>;
export type LabelSize = NonNullable<LabelRecipeProps["size"]>;
export type LabelTone = NonNullable<LabelRecipeProps["tone"]>;
export type LabelWeight = NonNullable<LabelRecipeProps["weight"]>;

export type LabelProps = Omit<
  React.LabelHTMLAttributes<HTMLLabelElement>,
  "className" | "color"
> &
  LabelRecipeProps & {
    className?: string;
    optional?: React.ReactNode;
    required?: boolean;
    requiredIndicator?: React.ReactNode;
  };

export const Label = React.forwardRef<HTMLLabelElement, LabelProps>(
  function Label(
    {
      children,
      className,
      optional,
      required = false,
      requiredIndicator = "*",
      size = "md",
      tone = "default",
      truncate = false,
      variant = "standard",
      weight = variant === "eyebrow" ? "semibold" : "medium",
      ...props
    },
    ref,
  ) {
    return (
      <label
        ref={ref}
        className={labelVariants({
          className,
          size,
          tone,
          truncate,
          variant,
          weight,
        })}
        {...props}
      >
        <span className={truncate ? "truncate" : undefined}>{children}</span>
        <RequiredMark required={required} indicator={requiredIndicator} />
        <OptionalHint optional={optional} />
      </label>
    );
  },
);
Label.displayName = "Label";

export interface RequiredMarkProps {
  required?: boolean;
  /** The mark glyph/text (defaults to `*`). */
  indicator?: React.ReactNode;
  /** Extra classes — pass `ml-1` where the parent has no gap. */
  className?: string;
}

/**
 * The decorative required-field mark — `aria-hidden`, since the accessible
 * required state lives on the control, not the label. The one owner shared by
 * `Label`, `FieldLabel`, and the form-layout `FieldRow`.
 */
export function RequiredMark({
  required = false,
  indicator = "*",
  className,
}: RequiredMarkProps): React.ReactElement | null {
  if (!required) return null;
  return (
    <span className={cn("text-danger-text", className)} aria-hidden="true">
      {indicator}
    </span>
  );
}

export interface OptionalHintProps {
  /** The hint content (e.g. "optional"); nothing renders when absent. */
  optional?: React.ReactNode;
  /** Extra classes — pass `ml-1` where the parent has no gap. */
  className?: string;
}

/** The muted hint shown after a label for an optional field. */
export function OptionalHint({
  optional,
  className,
}: OptionalHintProps): React.ReactElement | null {
  if (!optional) return null;
  return (
    <span className={cn("font-normal text-fg-muted", className)}>{optional}</span>
  );
}

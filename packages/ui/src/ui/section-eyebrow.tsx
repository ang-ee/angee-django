import * as React from "react";

import { toneText } from "../lib/tones";
import { tv, type VariantProps } from "../lib/variants";

export const sectionEyebrowVariants = tv({
  base: "m-0 uppercase",
  variants: {
    size: {
      xs: "text-2xs",
      sm: "text-xs",
    },
    tone: {
      muted: "text-fg-muted",
      fg: "text-fg",
      brand: "text-brand",
      danger: toneText("danger"),
    },
    weight: {
      medium: "font-medium",
      semibold: "font-semibold",
    },
    tracking: {
      normal: "tracking-normal",
      wide: "tracking-wide",
      wider: "tracking-wider",
    },
    spacing: {
      none: "",
      menu: "px-2 pb-1",
      field: "mb-1 block",
    },
    truncate: {
      true: "truncate",
      false: "",
    },
  },
  defaultVariants: {
    size: "xs",
    tone: "muted",
    weight: "semibold",
    tracking: "wide",
    spacing: "none",
    truncate: false,
  },
});

export type SectionEyebrowRecipeProps = VariantProps<
  typeof sectionEyebrowVariants
>;

export type SectionEyebrowSize = NonNullable<
  SectionEyebrowRecipeProps["size"]
>;
export type SectionEyebrowTone = NonNullable<
  SectionEyebrowRecipeProps["tone"]
>;
export type SectionEyebrowWeight = NonNullable<
  SectionEyebrowRecipeProps["weight"]
>;
export type SectionEyebrowTracking = NonNullable<
  SectionEyebrowRecipeProps["tracking"]
>;
export type SectionEyebrowSpacing = NonNullable<
  SectionEyebrowRecipeProps["spacing"]
>;

export type SectionEyebrowElement = "dt" | "h2" | "h3" | "label" | "p" | "span";

export type SectionEyebrowProps = Omit<
  React.HTMLAttributes<HTMLElement>,
  "className" | "color"
> &
  SectionEyebrowRecipeProps & {
    as?: SectionEyebrowElement;
    className?: string;
    htmlFor?: string;
  };

export const SectionEyebrow = React.forwardRef<HTMLElement, SectionEyebrowProps>(
  function SectionEyebrow(
    {
      as: Element = "p",
      className,
      size = "xs",
      spacing = "none",
      tone = "muted",
      tracking = "wide",
      truncate = false,
      weight = "semibold",
      ...props
    },
    ref,
  ) {
    return React.createElement(Element, {
      ref,
      className: sectionEyebrowVariants({
        className,
        size,
        spacing,
        tone,
        tracking,
        truncate,
        weight,
      }),
      ...props,
    });
  },
);
SectionEyebrow.displayName = "SectionEyebrow";

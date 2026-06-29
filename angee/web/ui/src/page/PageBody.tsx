import * as React from "react";

import { tv, type VariantProps } from "../lib/variants";

export const pageBodyVariants = tv({
  base: "min-h-0 min-w-0 flex-1 bg-canvas",
  variants: {
    gutter: {
      none: "p-0",
      compact: "p-4",
      comfortable: "p-5",
    },
    scroll: {
      auto: "overflow-auto",
      hidden: "overflow-hidden",
      visible: "overflow-visible",
    },
  },
  defaultVariants: {
    gutter: "comfortable",
    scroll: "auto",
  },
});

type PageBodyRecipeProps = VariantProps<typeof pageBodyVariants>;
type PageBodyElement = "main" | "section" | "div";

export type PageBodyProps = React.HTMLAttributes<HTMLElement> &
  PageBodyRecipeProps & {
    as?: PageBodyElement;
    className?: string;
  };

export const PageBody = React.forwardRef<HTMLElement, PageBodyProps>(
  function PageBody(
    { as = "main", className, gutter = "comfortable", scroll = "auto", ...props },
    ref,
  ) {
    return React.createElement(as, {
      ref,
      className: pageBodyVariants({ className, gutter, scroll }),
      ...props,
    });
  },
);
PageBody.displayName = "PageBody";

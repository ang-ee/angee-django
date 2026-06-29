import * as React from "react";

import { barVariants } from "../layouts/bar";
import { tv, type VariantProps } from "../lib/variants";

export const pageFooterVariants = tv({
  // The bar recipe owns the footer's chrome shell; only the per-density padding
  // and the sticky knob below stay footer-specific.
  base: barVariants({
    edge: "top",
    tone: "sheet",
    gap: 2,
    justify: "end",
    text: "13-muted",
  }),
  variants: {
    sticky: {
      true: "sticky bottom-0 z-sticky-cell",
      false: "",
    },
    density: {
      compact: "px-4 py-2",
      comfortable: "px-5 py-3",
    },
  },
  defaultVariants: {
    sticky: false,
    density: "comfortable",
  },
});

type PageFooterRecipeProps = VariantProps<typeof pageFooterVariants>;

export type PageFooterProps = React.HTMLAttributes<HTMLElement> &
  PageFooterRecipeProps & {
    className?: string;
  };

export const PageFooter = React.forwardRef<HTMLElement, PageFooterProps>(
  function PageFooter(
    { className, density = "comfortable", sticky = false, ...props },
    ref,
  ) {
    return (
      <footer
        ref={ref}
        className={pageFooterVariants({ className, density, sticky })}
        {...props}
      />
    );
  },
);
PageFooter.displayName = "PageFooter";

import * as React from "react";

import { tv, type VariantProps } from "../lib/variants";

export const pageFooterVariants = tv({
  base:
    "flex shrink-0 items-center justify-end gap-2 border-t border-border-subtle bg-sheet text-13 text-fg-muted",
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

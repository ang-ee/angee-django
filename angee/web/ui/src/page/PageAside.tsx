import * as React from "react";

import { tv, type VariantProps } from "../lib/variants";

export const pageAsideVariants = tv({
  base: "min-h-0 shrink-0 overflow-auto border-l border-border-subtle bg-sheet",
  variants: {
    width: {
      sm: "w-64",
      md: "w-80",
      lg: "w-[23.75rem]",
    },
    sticky: {
      true: "sticky top-0 self-start",
      false: "",
    },
    collapse: {
      never: "",
      belowLg: "hidden lg:block",
    },
    gutter: {
      none: "p-0",
      compact: "p-4",
      comfortable: "p-5",
    },
  },
  defaultVariants: {
    width: "md",
    sticky: false,
    collapse: "belowLg",
    gutter: "comfortable",
  },
});

type PageAsideRecipeProps = VariantProps<typeof pageAsideVariants>;

export type PageAsideProps = React.HTMLAttributes<HTMLElement> &
  PageAsideRecipeProps & {
    className?: string;
  };

export const PageAside = React.forwardRef<HTMLElement, PageAsideProps>(
  function PageAside(
    {
      className,
      collapse = "belowLg",
      gutter = "comfortable",
      sticky = false,
      width = "md",
      ...props
    },
    ref,
  ) {
    return (
      <aside
        ref={ref}
        className={pageAsideVariants({
          className,
          collapse,
          gutter,
          sticky,
          width,
        })}
        {...props}
      />
    );
  },
);
PageAside.displayName = "PageAside";

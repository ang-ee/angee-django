import * as React from "react";

import { tv, type VariantProps } from "../lib/variants";

export const pageVariants = tv({
  base: "flex min-h-0 flex-col bg-canvas text-fg",
  variants: {
    height: {
      auto: "",
      fill: "min-h-full flex-1",
      viewport: "h-screen",
    },
    overflow: {
      hidden: "overflow-hidden",
      visible: "overflow-visible",
    },
  },
  defaultVariants: {
    height: "fill",
    overflow: "hidden",
  },
});

type PageRecipeProps = VariantProps<typeof pageVariants>;

export type PageProps = React.HTMLAttributes<HTMLDivElement> &
  PageRecipeProps & {
    className?: string;
  };

/**
 * The page frame: a vertical column (header → toolbar → body, with optional
 * aside/footer) that fills its container. It is layout-agnostic — when mounted
 * inside a console content region that already scrolls, run `height="auto"` /
 * `overflow="visible"` so the region keeps owning the scroll and canvas
 * background; use the `fill`/`viewport` heights only when the page owns its own
 * viewport.
 */
export const Page = React.forwardRef<HTMLDivElement, PageProps>(function Page(
  { className, height = "fill", overflow = "hidden", ...props },
  ref,
) {
  return (
    <div
      ref={ref}
      className={pageVariants({ className, height, overflow })}
      {...props}
    />
  );
});
Page.displayName = "Page";

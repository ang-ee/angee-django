import * as React from "react";

import { tv, type VariantProps } from "../lib/variants";
import { Toolbar } from "../ui/toolbar";

export const pageToolbarVariants = tv({
  slots: {
    root:
      "flex min-h-control-h shrink-0 items-center gap-3 border-b border-border-subtle bg-sheet px-4 py-2",
    start: "flex min-w-0 flex-1 flex-wrap items-center gap-2",
    end: "flex shrink-0 flex-wrap items-center justify-end gap-2",
  },
  variants: {
    sticky: {
      true: { root: "sticky top-0 z-sticky-cell" },
      false: { root: "" },
    },
    density: {
      compact: { root: "min-h-11 px-3 py-1.5" },
      comfortable: { root: "min-h-control-h px-4 py-2" },
    },
  },
  defaultVariants: {
    sticky: false,
    density: "comfortable",
  },
});

type PageToolbarRecipeProps = VariantProps<typeof pageToolbarVariants>;

export type PageToolbarProps = React.HTMLAttributes<HTMLDivElement> &
  PageToolbarRecipeProps & {
    children?: React.ReactNode;
    className?: string;
    end?: React.ReactNode;
    start?: React.ReactNode;
  };

export const PageToolbar = React.forwardRef<HTMLDivElement, PageToolbarProps>(
  function PageToolbar(
    {
      children,
      className,
      density = "comfortable",
      end,
      start,
      sticky = false,
      ...props
    },
    ref,
  ) {
    const styles = pageToolbarVariants({ density, sticky });
    const freeform = hasRenderableNode(children);
    const hasStart = hasRenderableNode(start);
    const hasEnd = hasRenderableNode(end);

    if (!freeform && !hasStart && !hasEnd) return null;

    return (
      <Toolbar.Root
        ref={ref}
        className={styles.root({ className })}
        {...props}
      >
        {freeform ? (
          children
        ) : (
          <>
            {hasStart ? (
              <Toolbar.Group className={styles.start()}>{start}</Toolbar.Group>
            ) : null}
            {hasEnd ? (
              <Toolbar.Group className={styles.end()}>{end}</Toolbar.Group>
            ) : null}
          </>
        )}
      </Toolbar.Root>
    );
  },
);
PageToolbar.displayName = "PageToolbar";

function hasRenderableNode(node: React.ReactNode): boolean {
  return React.Children.toArray(node).length > 0;
}

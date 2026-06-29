import * as React from "react";
import { ScrollArea as BaseScrollArea } from "@base-ui/react/scroll-area";
import type {
  ScrollAreaContentProps as BaseScrollAreaContentProps,
  ScrollAreaCornerProps as BaseScrollAreaCornerProps,
  ScrollAreaRootProps as BaseScrollAreaRootProps,
  ScrollAreaScrollbarProps as BaseScrollAreaScrollbarProps,
  ScrollAreaThumbProps as BaseScrollAreaThumbProps,
  ScrollAreaViewportProps as BaseScrollAreaViewportProps,
} from "@base-ui/react/scroll-area";

import { tv, type VariantProps } from "../lib/variants";

export const scrollAreaVariants = tv({
  slots: {
    root: "relative min-h-0 overflow-hidden",
    viewport: "h-full w-full overscroll-contain",
    content: "min-w-full",
    scrollbar:
      "absolute flex touch-none select-none p-0.5 opacity-0 transition-opacity data-[hovering]:opacity-100 data-[scrolling]:opacity-100 data-[orientation=vertical]:right-0 data-[orientation=vertical]:top-0 data-[orientation=vertical]:h-full data-[orientation=vertical]:w-2.5 data-[orientation=horizontal]:bottom-0 data-[orientation=horizontal]:left-0 data-[orientation=horizontal]:h-2.5 data-[orientation=horizontal]:w-full",
    thumb:
      "flex-1 rounded-full bg-border-strong transition-colors hover:bg-fg-muted",
    corner: "bg-transparent",
  },
});

export type ScrollAreaRecipeProps = VariantProps<typeof scrollAreaVariants>;

export type ScrollAreaScrollbarPolicy =
  | "both"
  | "horizontal"
  | "none"
  | "vertical";

export type ScrollAreaRootProps = BaseScrollAreaRootProps & {
  className?: string;
};
export type ScrollAreaViewportProps = BaseScrollAreaViewportProps & {
  className?: string;
};
export type ScrollAreaContentProps = BaseScrollAreaContentProps & {
  className?: string;
};
export type ScrollAreaScrollbarProps = BaseScrollAreaScrollbarProps & {
  className?: string;
};
export type ScrollAreaThumbProps = BaseScrollAreaThumbProps & {
  className?: string;
};
export type ScrollAreaCornerProps = BaseScrollAreaCornerProps & {
  className?: string;
};

export const ScrollAreaRoot = React.forwardRef<
  HTMLDivElement,
  ScrollAreaRootProps
>(function ScrollAreaRoot({ className, ...props }, ref) {
  const styles = scrollAreaVariants();
  return (
    <BaseScrollArea.Root
      ref={ref}
      className={styles.root({ className })}
      {...props}
    />
  );
});
ScrollAreaRoot.displayName = "ScrollAreaRoot";

export const ScrollAreaViewport = React.forwardRef<
  HTMLDivElement,
  ScrollAreaViewportProps
>(function ScrollAreaViewport({ className, ...props }, ref) {
  const styles = scrollAreaVariants();
  return (
    <BaseScrollArea.Viewport
      ref={ref}
      className={styles.viewport({ className })}
      {...props}
    />
  );
});
ScrollAreaViewport.displayName = "ScrollAreaViewport";

export const ScrollAreaContent = React.forwardRef<
  HTMLDivElement,
  ScrollAreaContentProps
>(function ScrollAreaContent({ className, ...props }, ref) {
  const styles = scrollAreaVariants();
  return (
    <BaseScrollArea.Content
      ref={ref}
      className={styles.content({ className })}
      {...props}
    />
  );
});
ScrollAreaContent.displayName = "ScrollAreaContent";

export const ScrollAreaScrollbar = React.forwardRef<
  HTMLDivElement,
  ScrollAreaScrollbarProps
>(function ScrollAreaScrollbar({ className, ...props }, ref) {
  const styles = scrollAreaVariants();
  return (
    <BaseScrollArea.Scrollbar
      ref={ref}
      className={styles.scrollbar({ className })}
      {...props}
    />
  );
});
ScrollAreaScrollbar.displayName = "ScrollAreaScrollbar";

export const ScrollAreaThumb = React.forwardRef<
  HTMLDivElement,
  ScrollAreaThumbProps
>(function ScrollAreaThumb({ className, ...props }, ref) {
  const styles = scrollAreaVariants();
  return (
    <BaseScrollArea.Thumb
      ref={ref}
      className={styles.thumb({ className })}
      {...props}
    />
  );
});
ScrollAreaThumb.displayName = "ScrollAreaThumb";

export const ScrollAreaCorner = React.forwardRef<
  HTMLDivElement,
  ScrollAreaCornerProps
>(function ScrollAreaCorner({ className, ...props }, ref) {
  const styles = scrollAreaVariants();
  return (
    <BaseScrollArea.Corner
      ref={ref}
      className={styles.corner({ className })}
      {...props}
    />
  );
});
ScrollAreaCorner.displayName = "ScrollAreaCorner";

export type ScrollAreaProps = Omit<ScrollAreaRootProps, "children"> &
  {
    children: React.ReactNode;
    contentClassName?: string;
    scrollbarClassName?: string;
    scrollbars?: ScrollAreaScrollbarPolicy;
    thumbClassName?: string;
    viewportClassName?: string;
  };

export const ScrollArea = React.forwardRef<HTMLDivElement, ScrollAreaProps>(
  function ScrollArea(
    {
      children,
      className,
      contentClassName,
      scrollbarClassName,
      scrollbars = "vertical",
      thumbClassName,
      viewportClassName,
      ...props
    },
    ref,
  ) {
    const showVertical = scrollbars === "vertical" || scrollbars === "both";
    const showHorizontal = scrollbars === "horizontal" || scrollbars === "both";
    return (
      <ScrollAreaRoot ref={ref} className={className} {...props}>
        <ScrollAreaViewport className={viewportClassName}>
          <ScrollAreaContent className={contentClassName}>
            {children}
          </ScrollAreaContent>
        </ScrollAreaViewport>
        {showVertical ? (
          <ScrollAreaScrollbar
            className={scrollbarClassName}
            orientation="vertical"
          >
            <ScrollAreaThumb className={thumbClassName} />
          </ScrollAreaScrollbar>
        ) : null}
        {showHorizontal ? (
          <ScrollAreaScrollbar
            className={scrollbarClassName}
            orientation="horizontal"
          >
            <ScrollAreaThumb className={thumbClassName} />
          </ScrollAreaScrollbar>
        ) : null}
        {showVertical && showHorizontal ? <ScrollAreaCorner /> : null}
      </ScrollAreaRoot>
    );
  },
);
ScrollArea.displayName = "ScrollArea";

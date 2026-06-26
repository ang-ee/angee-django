import * as React from "react";
import {
  Group as ResizableGroup,
  Panel as ResizablePanel,
  Separator as ResizableSeparator,
  type GroupProps as ResizableGroupProps,
  type Orientation as ResizableOrientation,
  type PanelProps as ResizablePanelProps,
  type SeparatorProps as ResizableSeparatorProps,
} from "react-resizable-panels";

import { tv, type VariantProps } from "../lib/variants";

export const splitPanesVariants = tv({
  slots: {
    group: "min-h-0 min-w-0 flex-1 overflow-hidden",
    pane: "min-h-0 min-w-0 overflow-hidden",
    handle:
      "shrink-0 bg-border-subtle outline-none transition-colors hover:bg-border-strong focus-visible:focus-ring data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50",
  },
  variants: {
    direction: {
      horizontal: {
        group: "h-full",
        handle: "w-px cursor-col-resize",
      },
      vertical: {
        group: "w-full",
        handle: "h-px cursor-row-resize",
      },
    },
  },
  defaultVariants: {
    direction: "horizontal",
  },
});

type SplitPanesRecipeProps = VariantProps<typeof splitPanesVariants>;
const SplitPanesDirectionContext =
  React.createContext<ResizableOrientation>("horizontal");

export type SplitPanesProps = Omit<
  ResizableGroupProps,
  "className" | "orientation"
> &
  SplitPanesRecipeProps & {
    className?: string;
    direction?: ResizableOrientation;
  };

export const SplitPanes = React.forwardRef<HTMLDivElement, SplitPanesProps>(
  function SplitPanes(
    { className, direction = "horizontal", ...props },
    ref,
  ) {
    const styles = splitPanesVariants({ direction });

    return (
      <SplitPanesDirectionContext.Provider value={direction}>
        <ResizableGroup
          elementRef={ref}
          orientation={direction}
          className={styles.group({ className })}
          {...props}
        />
      </SplitPanesDirectionContext.Provider>
    );
  },
);
SplitPanes.displayName = "SplitPanes";

export type SplitPaneProps = Omit<ResizablePanelProps, "className"> & {
  className?: string;
};

// react-resizable-panels v4 reads a bare number as *pixels* and an
// unsuffixed string as a *percentage*. The framework convention is the
// pre-v4 one — a bare number is a percentage — so normalise numbers to
// percent strings here; pass an explicit `"…px"` string for pixel sizing
// (as `Chatter` does). One place owns the unit so every consumer is spared it.
function asPercent(
  size: number | string | undefined,
): number | string | undefined {
  return typeof size === "number" ? `${size}` : size;
}

export const SplitPane = React.forwardRef<HTMLDivElement, SplitPaneProps>(
  function SplitPane(
    { className, defaultSize, minSize, maxSize, collapsedSize, ...props },
    ref,
  ) {
    const styles = splitPanesVariants();

    return (
      <ResizablePanel
        elementRef={ref}
        className={styles.pane({ className })}
        defaultSize={asPercent(defaultSize)}
        minSize={asPercent(minSize)}
        maxSize={asPercent(maxSize)}
        collapsedSize={asPercent(collapsedSize)}
        {...props}
      />
    );
  },
);
SplitPane.displayName = "SplitPane";

export type SplitPaneHandleProps = Omit<
  ResizableSeparatorProps,
  "className"
> &
  SplitPanesRecipeProps & {
    className?: string;
  };

export const SplitPaneHandle = React.forwardRef<
  HTMLDivElement,
  SplitPaneHandleProps
>(function SplitPaneHandle(
  { className, direction, ...props },
  ref,
) {
  const contextDirection = React.useContext(SplitPanesDirectionContext);
  const resolvedDirection = direction ?? contextDirection;
  const styles = splitPanesVariants({ direction: resolvedDirection });

  return (
    <ResizableSeparator
      elementRef={ref}
      className={styles.handle({ className })}
      {...props}
    />
  );
});
SplitPaneHandle.displayName = "SplitPaneHandle";

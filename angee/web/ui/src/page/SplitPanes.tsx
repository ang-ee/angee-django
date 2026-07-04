import * as React from "react";
import {
  Group as ResizableGroup,
  Panel as ResizablePanel,
  Separator as ResizableSeparator,
  useDefaultLayout,
  usePanelCallbackRef,
  type GroupProps as ResizableGroupProps,
  type LayoutStorage,
  type OnPanelResize,
  type Orientation as ResizableOrientation,
  type PanelImperativeHandle,
  type PanelProps as ResizablePanelProps,
  type SeparatorProps as ResizableSeparatorProps,
} from "react-resizable-panels";

import { browserLocalStorage } from "../lib/browser-storage";
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
    /**
     * Persistence id for the pane layout. When set — and a browser `Storage`
     * is reachable — the layout for this id is read on mount (`defaultLayout`)
     * and saved after each resize (`onLayoutChanged`), so the panes reopen at
     * their last sizes. With no id stored, or no id at all, the panes fall back
     * to each `SplitPane`'s `defaultSize`.
     */
    autoSave?: string;
    /**
     * The ids of the panes actually rendered. For a group with
     * conditionally-rendered panes, this lets the library save/restore a
     * separate layout per panel set, so a set never restores sizes saved for a
     * different one (react-resizable-panels `useDefaultLayout` `panelIds`).
     */
    panelIds?: string[];
  };

// react-resizable-panels persists through a `Storage` (localStorage by
// default). Exported as the layout owner so other layout state (the drawer
// open/closed flags) reaches localStorage the same way.
export function layoutStorage(): Storage | null {
  return browserLocalStorage();
}

// A `Group` that reads/writes its layout through the library's own persistence
// owner. Split out so the `useDefaultLayout` hook (which touches `Storage`)
// runs unconditionally and only when persistence is actually wired.
function PersistentGroup({
  storageId,
  storage,
  panelIds,
  ...groupProps
}: {
  storageId: string;
  storage: LayoutStorage;
  panelIds?: string[];
} & Omit<
  ResizableGroupProps,
  "defaultLayout" | "onLayoutChange" | "onLayoutChanged"
>): React.ReactElement {
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({
    id: storageId,
    storage,
    ...(panelIds != null ? { panelIds } : {}),
  });
  return (
    <ResizableGroup
      defaultLayout={defaultLayout}
      onLayoutChanged={onLayoutChanged}
      {...groupProps}
    />
  );
}

export const SplitPanes = React.forwardRef<HTMLDivElement, SplitPanesProps>(
  function SplitPanes(
    { autoSave, panelIds, className, direction = "horizontal", ...props },
    ref,
  ) {
    const styles = splitPanesVariants({ direction });
    const storage = layoutStorage();

    return (
      <SplitPanesDirectionContext.Provider value={direction}>
        {autoSave != null && storage != null ? (
          <PersistentGroup
            // Remount per id so a different mode's stored layout (and each
            // SplitPane's defaultSize) re-applies — both are mount-only.
            key={autoSave}
            storageId={autoSave}
            storage={storage}
            panelIds={panelIds}
            elementRef={ref}
            orientation={direction}
            className={styles.group({ className })}
            {...props}
          />
        ) : (
          <ResizableGroup
            elementRef={ref}
            orientation={direction}
            className={styles.group({ className })}
            {...props}
          />
        )}
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

export type UseCollapsiblePaneOptions = {
  /**
   * Percentage (0..100) at or below which the pane reads as collapsed. The v4
   * `isCollapsed()` handle drives the authoritative value; this only widens the
   * threshold for panes given a non-zero `collapsedSize`. Defaults to 0.
   */
  collapsedSize?: number;
  /** Collapsed state assumed before the first resize fires. Defaults to false. */
  defaultCollapsed?: boolean;
};

export type CollapsiblePane = {
  /** Spread onto a `<SplitPane collapsible>` to capture its imperative handle. */
  panelRef: (handle: PanelImperativeHandle | null) => void;
  /** Spread onto the same `<SplitPane>` so `collapsed` tracks drag-to-collapse. */
  onResize: NonNullable<OnPanelResize>;
  /** Reactive — recomputed in `onResize`, since v4 `isCollapsed()` is not. */
  collapsed: boolean;
  /** Collapse if expanded, expand if collapsed. */
  toggle: () => void;
  collapse: () => void;
  expand: () => void;
};

/**
 * The in-stack handle that lets a chrome toggle drive a collapsible `SplitPane`.
 *
 * Wraps v4's `PanelImperativeHandle` (collapse/expand/isCollapsed/getSize/
 * resize) — reached through `usePanelCallbackRef` so the handle becomes
 * available reactively once the panel mounts — and adds the one thing v4 lacks:
 * a reactive `collapsed` flag, since `isCollapsed()` is non-reactive. Spread
 * `panelRef` and `onResize` onto a `<SplitPane collapsible>`; read `collapsed`
 * and call `toggle()`/`collapse()`/`expand()` from a sibling control.
 */
export function useCollapsiblePane(
  opts?: UseCollapsiblePaneOptions,
): CollapsiblePane {
  const { collapsedSize = 0, defaultCollapsed = false } = opts ?? {};
  const [handle, panelRef] = usePanelCallbackRef();
  const [collapsed, setCollapsed] = React.useState(defaultCollapsed);
  const handleRef = React.useRef<PanelImperativeHandle | null>(handle);
  const panelRefRef = React.useRef(panelRef);
  handleRef.current = handle;
  panelRefRef.current = panelRef;

  const stablePanelRef = React.useCallback(
    (next: PanelImperativeHandle | null) => panelRefRef.current(next),
    [],
  );

  const onResize = React.useCallback<NonNullable<OnPanelResize>>(
    (panelSize) => {
      const current = handleRef.current;
      setCollapsed(
        panelSize.asPercentage <= collapsedSize ||
          (current?.isCollapsed() ?? false),
      );
    },
    [collapsedSize],
  );

  const collapse = React.useCallback(() => handleRef.current?.collapse(), []);
  const expand = React.useCallback(() => handleRef.current?.expand(), []);
  const toggle = React.useCallback(() => {
    const current = handleRef.current;
    if (!current) return;
    if (current.isCollapsed()) current.expand();
    else current.collapse();
  }, []);

  // Stable object identity (changes only when `collapsed` flips) so a consumer
  // that publishes the controller through an effect does not re-fire every render.
  return React.useMemo(
    () => ({
      panelRef: stablePanelRef,
      onResize,
      collapsed,
      toggle,
      collapse,
      expand,
    }),
    [stablePanelRef, onResize, collapsed, toggle, collapse, expand],
  );
}

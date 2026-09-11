import * as React from "react";

import { cn } from "../lib/cn";
import { useContainerQuery } from "../lib/use-container-query";
import {
  SplitPane,
  SplitPaneHandle,
  SplitPanes,
  useCollapsiblePane,
  type CollapsiblePane,
} from "../page";

/**
 * The Workbench layout — the single collapsible/resizable multi-pane inner-shell
 * owner. It composes the `SplitPanes` primitives into the VS Code / Zed slot
 * vocabulary: a `primary` sidebar | the main content | a `secondary` sidebar.
 * Every side pane is collapsible, resizable, and size-persistent through the
 * panel group's `autoSave` id.
 *
 * Panes render only when supplied — `<Workbench>{content}</Workbench>` is a
 * bare content frame with no resize machinery, and adding `primary`/`secondary`
 * grows it into the full shell. The prop semantics mirror the older `Explorer`
 * so its consumers migrate mechanically (`navigator`→`primary`, `aside`→
 * `secondary`, `navigatorSize`→`primarySize`, `asideSize`→`secondarySize`).
 *
 * Collapsible panes are driven by `useCollapsiblePane` controllers that the
 * Workbench can surface upward, so sibling chrome toggles (e.g. the console
 * TopBar) can collapse/expand them and stay in sync with drag-to-collapse.
 */
export interface WorkbenchProps {
  /** Primary (left) sidebar pane — e.g. a navigator/sub-nav tree. */
  primary?: React.ReactNode;
  /** Main content (list / gallery / canvas / record). */
  children: React.ReactNode;
  /** Secondary (right) sidebar pane — e.g. a preview/inspector/chatter. */
  secondary?: React.ReactNode;
  /** Persistence id for the pane sizes. */
  autoSave?: string;
  /** Primary pane default width, percent. */
  primarySize?: number;
  /** Secondary pane default width, percent. */
  secondarySize?: number;
  /** Minimum content pane size, percent. */
  contentMinSize?: number;
  /** Minimum secondary pane size, percent. */
  secondaryMinSize?: number;
  /** Stack the secondary pane below content when this container is narrower. */
  stackBelow?: number;
  /** Start the secondary pane collapsed when no persisted layout exists. */
  secondaryDefaultCollapsed?: boolean;
  /** Receives the primary pane's collapse controller (or null on unmount). */
  onPrimaryController?: (controller: CollapsiblePane | null) => void;
  /** Receives the secondary pane's collapse controller (or null on unmount). */
  onSecondaryController?: (controller: CollapsiblePane | null) => void;
  /** Whether the content pane is clipped by the workbench or allowed to grow the document. */
  scrollMode?: "contained" | "browser";
  className?: string;
}

// Publish a pane's controller upward whenever it changes (its reactive
// `collapsed` flag flips identity), and clear it on unmount so a stale bridge
// never drives a torn-down pane.
function usePublishedController(
  controller: CollapsiblePane,
  publish: ((controller: CollapsiblePane | null) => void) | undefined,
  active: boolean,
): void {
  React.useEffect(() => {
    publish?.(active ? controller : null);
  }, [publish, controller, active]);
  React.useEffect(() => () => publish?.(null), [publish]);
}

export function Workbench({
  primary,
  children,
  secondary,
  autoSave,
  primarySize = 18,
  secondarySize = 26,
  contentMinSize,
  secondaryMinSize = 16,
  stackBelow,
  secondaryDefaultCollapsed = false,
  onPrimaryController,
  onSecondaryController,
  scrollMode = "contained",
  className,
}: WorkbenchProps): React.ReactElement {
  const hasPrimary = primary != null;
  const hasSecondary = secondary != null;
  const browserScroll = scrollMode === "browser";

  // Controllers stay inert when their pane is not rendered (their imperative
  // handles simply never mount).
  const primaryController = useCollapsiblePane();
  const [containerRef, sideBySide] = useContainerQuery(stackBelow ?? 0);
  const stacked = stackBelow !== undefined && !sideBySide;
  const secondaryController = useCollapsiblePane({
    defaultCollapsed: secondaryDefaultCollapsed,
    expandedSize: secondarySize,
  });
  usePublishedController(primaryController, onPrimaryController, hasPrimary);
  usePublishedController(secondaryController, onSecondaryController, hasSecondary);

  // No panes → a plain content frame, no resize machinery (Explorer's pattern).
  if (!hasPrimary && !hasSecondary) {
    return (
      <div
        className={cn(
          browserScroll
            ? "h-full min-h-0 min-w-0 overflow-visible"
            : "h-full min-h-0 min-w-0",
          className,
        )}
      >
        {children}
      </div>
    );
  }

  // The present panel set, so the library restores the layout that matches what
  // is actually rendered — a conditionally-present primary pane otherwise
  // restores sizes saved for a different set (react-resizable-panels `panelIds`).
  const panelIds = [
    ...(hasPrimary ? ["primary"] : []),
    "content",
    ...(hasSecondary ? ["secondary"] : []),
  ];

  return (
    <SplitPanes
      ref={containerRef}
      direction={stacked ? "vertical" : "horizontal"}
      autoSave={stackBelow === undefined || autoSave === undefined ? autoSave : `${autoSave}.${stacked ? "stacked" : "side-by-side"}`}
      panelIds={panelIds}
      className={cn(
        "h-full min-h-0",
        browserScroll && "overflow-visible",
        className,
      )}
    >
      {hasPrimary ? (
        <>
          <SplitPane
            id="primary"
            defaultSize={primarySize}
            minSize={12}
            collapsible
            panelRef={primaryController.panelRef}
            onResize={primaryController.onResize}
            className={cn("min-h-0 min-w-0 bg-sheet-2", stacked ? "border-b border-border-subtle" : "border-r border-border-subtle")}
          >
            {primary}
          </SplitPane>
          <SplitPaneHandle />
        </>
      ) : null}
      <SplitPane
        id="content"
        minSize={contentMinSize}
        className={cn(
          "min-h-0 min-w-0 bg-canvas",
          browserScroll && "overflow-visible",
        )}
      >
        {children}
      </SplitPane>
      {hasSecondary ? (
        <>
          <SplitPaneHandle />
          <SplitPane
            id="secondary"
            defaultSize={secondaryDefaultCollapsed ? 0 : secondarySize}
            minSize={secondaryMinSize}
            collapsible
            collapsedSize={0}
            panelRef={secondaryController.panelRef}
            onResize={secondaryController.onResize}
            className={cn("min-h-0 min-w-0 bg-sheet-2", stacked ? "border-t border-border-subtle" : "border-l border-border-subtle")}
          >
            {secondary}
          </SplitPane>
        </>
      ) : null}
    </SplitPanes>
  );
}

import * as React from "react";

import { cn } from "../lib/cn";
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
  /** Receives the primary pane's collapse controller (or null on unmount). */
  onPrimaryController?: (controller: CollapsiblePane | null) => void;
  /** Receives the secondary pane's collapse controller (or null on unmount). */
  onSecondaryController?: (controller: CollapsiblePane | null) => void;
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
  onPrimaryController,
  onSecondaryController,
  className,
}: WorkbenchProps): React.ReactElement {
  const hasPrimary = primary != null;
  const hasSecondary = secondary != null;

  // Controllers stay inert when their pane is not rendered (their imperative
  // handles simply never mount).
  const primaryController = useCollapsiblePane();
  const secondaryController = useCollapsiblePane();
  usePublishedController(primaryController, onPrimaryController, hasPrimary);
  usePublishedController(secondaryController, onSecondaryController, hasSecondary);

  // No panes → a plain content frame, no resize machinery (Explorer's pattern).
  if (!hasPrimary && !hasSecondary) {
    return (
      <div className={cn("h-full min-h-0 min-w-0", className)}>{children}</div>
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
      direction="horizontal"
      autoSave={autoSave}
      panelIds={panelIds}
      className={cn("h-full min-h-0", className)}
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
            className="min-h-0 min-w-0 border-r border-border-subtle bg-sheet-2"
          >
            {primary}
          </SplitPane>
          <SplitPaneHandle />
        </>
      ) : null}
      <SplitPane id="content" className="min-h-0 min-w-0 bg-canvas">
        {children}
      </SplitPane>
      {hasSecondary ? (
        <>
          <SplitPaneHandle />
          <SplitPane
            id="secondary"
            defaultSize={secondarySize}
            minSize={16}
            collapsible
            panelRef={secondaryController.panelRef}
            onResize={secondaryController.onResize}
            className="min-h-0 min-w-0 border-l border-border-subtle bg-sheet-2"
          >
            {secondary}
          </SplitPane>
        </>
      ) : null}
    </SplitPanes>
  );
}

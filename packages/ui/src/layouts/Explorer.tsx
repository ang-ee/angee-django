import * as React from "react";

import { cn } from "../lib/cn";
import { SplitPane, SplitPaneHandle, SplitPanes } from "../page";

/**
 * The Explorer layout — a resizable three-pane frame: a `navigator` (e.g. a
 * vault/folder `TreeView`), the main content (a list or `GalleryView`), and an
 * optional `aside` (e.g. a `PreviewPane`). Panes render only when supplied, so
 * `<Explorer>{list}</Explorer>` is a bare content pane and adding `navigator`/
 * `aside` grows it into the storage/knowledge file explorer. Sizes persist per
 * `autoSave` id. This is the `ResourceList navigator ⇒ Explorer` shape from the
 * page docs, composed from the resizable split primitives.
 */
export interface ExplorerProps {
  /** Left pane (folder/vault tree). */
  navigator?: React.ReactNode;
  /** Right pane (file/record preview). */
  aside?: React.ReactNode;
  /** Main content (list / gallery / record). */
  children: React.ReactNode;
  /** Persistence id for the pane sizes. */
  autoSave?: string;
  /** Navigator pane default width, percent. */
  navigatorSize?: number;
  /** Aside pane default width, percent. */
  asideSize?: number;
  className?: string;
}

export function Explorer({
  navigator,
  aside,
  children,
  autoSave,
  navigatorSize = 18,
  asideSize = 26,
  className,
}: ExplorerProps): React.ReactElement {
  // No side panes → a plain content frame, no resize machinery.
  if (!navigator && !aside) {
    return (
      <div className={cn("h-full min-h-0 min-w-0", className)}>{children}</div>
    );
  }

  return (
    <SplitPanes
      direction="horizontal"
      autoSave={autoSave}
      className={cn("h-full min-h-0", className)}
    >
      {navigator ? (
        <>
          <SplitPane
            defaultSize={navigatorSize}
            minSize={12}
            collapsible
            className="min-h-0 min-w-0 border-r border-border-subtle bg-sheet-2"
          >
            {navigator}
          </SplitPane>
          <SplitPaneHandle />
        </>
      ) : null}
      <SplitPane className="min-h-0 min-w-0 bg-canvas">{children}</SplitPane>
      {aside ? (
        <>
          <SplitPaneHandle />
          <SplitPane
            defaultSize={asideSize}
            minSize={16}
            collapsible
            className="min-h-0 min-w-0 border-l border-border-subtle bg-sheet-2"
          >
            {aside}
          </SplitPane>
        </>
      ) : null}
    </SplitPanes>
  );
}

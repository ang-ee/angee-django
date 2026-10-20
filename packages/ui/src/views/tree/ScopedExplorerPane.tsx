import * as React from "react";

import { EmptyState } from "../../fragments/EmptyState";
import { useUiT } from "../../i18n";
import { PaneToolbar, PaneToolbarHost, type PaneToolbarProps } from "../../layouts/PaneToolbar";
import { PrimaryPanePublisher } from "../../layouts/primary-pane-context";
import { Skeleton, SkeletonStatus, SkeletonText } from "../../ui/skeleton";
import {
  RelationPicker,
  type RelationCreateConfig,
} from "../relation/RelationPicker";
import {
  useScopedTreeExplorer,
  type ScopedTreeExplorerController,
  type UseScopedTreeExplorerOptions,
} from "./useScopedTreeExplorer";

export interface ScopedExplorerRootPicker {
  "aria-label": string;
  placeholder?: string;
  searchPlaceholder?: string;
  create?: RelationCreateConfig;
  onCreated?: (id: string) => void;
}

export type ScopedExplorerController<
  TRoot,
  TTreeRow extends { id: string },
> = ScopedTreeExplorerController<TRoot, TTreeRow>;

export interface ScopedExplorerPaneProps<
  TRoot,
  TTreeRow extends { id: string },
> extends UseScopedTreeExplorerOptions<TRoot, TTreeRow> {
  loading?: boolean;
  loadingContent?: React.ReactNode;
  emptyContent?: React.ReactNode;
  /** Render the owned content even when the caller has no visible roots. */
  renderContentWithoutRoots?: boolean;
  navigatorLabel: string;
  /** The toolbar's selector: the root (drive, vault) picker, created inline where allowed. */
  rootPicker: ScopedExplorerRootPicker;
  /** The rest of the toolbar row: the pane's scope, search, create verbs and overflow. */
  toolbar?: (
    controller: ScopedExplorerController<TRoot, TTreeRow>,
  ) => Omit<PaneToolbarProps, "selector" | "className">;
  onRootChange?: (
    rootId: string,
    controller: ScopedExplorerController<TRoot, TTreeRow>,
  ) => void;
  renderTree: (
    controller: ScopedExplorerController<TRoot, TTreeRow>,
  ) => React.ReactNode;
  renderNavigatorFooter?: (
    controller: ScopedExplorerController<TRoot, TTreeRow>,
  ) => React.ReactNode;
  children: (
    controller: ScopedExplorerController<TRoot, TTreeRow>,
  ) => React.ReactNode;
}

/**
 * Shared explorer page shell: the pane toolbar (root picker as its selector, the
 * addon's verbs beside it) over the scoped tree, published into the console
 * primary pane, while loading/empty root states own the main surface. Addons
 * keep the facts that are genuinely theirs: tree row projection, drop policy,
 * route transitions, and domain actions.
 */
export function ScopedExplorerPane<
  TRoot,
  TTreeRow extends { id: string },
>({
  loading = false,
  loadingContent,
  emptyContent,
  renderContentWithoutRoots = false,
  navigatorLabel,
  rootPicker,
  toolbar,
  onRootChange,
  renderTree,
  renderNavigatorFooter,
  children,
  ...options
}: ScopedExplorerPaneProps<TRoot, TTreeRow>): React.ReactElement {
  const t = useUiT();
  const controller = useScopedTreeExplorer(options);
  const { rootId, rootOptions, setRootId } = controller;
  const hasRoots = rootOptions.length > 0;
  const navigator = React.useMemo(
    () => (
      <PaneToolbarHost
        role="navigation"
        aria-label={navigatorLabel}
      >
        <PaneToolbar
          {...toolbar?.(controller)}
          selector={
            <div className="min-w-0 max-w-56 flex-1">
              <RelationPicker
                presentation="cell"
                aria-label={rootPicker["aria-label"]}
                value={rootId}
                options={rootOptions}
                placeholder={rootPicker.placeholder}
                searchPlaceholder={rootPicker.searchPlaceholder}
                create={rootPicker.create}
                onChange={(value) => {
                  setRootId(value);
                  onRootChange?.(value, controller);
                }}
                onCreated={(id) => {
                  setRootId(id);
                  rootPicker.onCreated?.(id);
                }}
              />
            </div>
          }
        />
        <div className="flex min-h-0 flex-1 flex-col gap-2 p-2">
          {renderTree(controller)}
          {renderNavigatorFooter?.(controller)}
        </div>
      </PaneToolbarHost>
    ),
    [
      controller,
      navigatorLabel,
      onRootChange,
      renderNavigatorFooter,
      renderTree,
      rootId,
      rootOptions,
      rootPicker,
      setRootId,
      toolbar,
    ],
  );
  const loadingLabel = t("loading.default");
  const primaryPane = hasRoots
    ? navigator
    : loading
      ? <ExplorerNavigatorSkeleton label={loadingLabel} />
      : null;

  if (loading && !hasRoots && !renderContentWithoutRoots) {
    return (
      <>
        <PrimaryPanePublisher node={primaryPane} />
        {loadingContent ?? <ExplorerContentSkeleton label={loadingLabel} />}
      </>
    );
  }
  if (!hasRoots && !renderContentWithoutRoots) {
    return (
      <>
        <PrimaryPanePublisher node={primaryPane} />
        {emptyContent ?? (
          <EmptyState
            fill
            icon="folder"
            title={t("explorer.emptyTitle")}
            description={t("explorer.emptyDescription")}
          />
        )}
      </>
    );
  }
  return (
    <>
      <PrimaryPanePublisher node={primaryPane} />
      {children(controller)}
    </>
  );
}

function ExplorerNavigatorSkeleton({ label }: { label: string }): React.ReactElement {
  return (
    <SkeletonStatus label={label} className="flex h-full min-h-0 flex-col gap-3 p-2">
      <div aria-hidden="true" className="space-y-3">
        <Skeleton className="h-9 w-full" />
        {Array.from({ length: 7 }, (_, index) => (
          <div key={index} className="flex items-center gap-2 px-2 py-1">
            <Skeleton className="size-3.5 shrink-0" />
            <Skeleton shape="text" size="sm" className={index % 3 === 0 ? "w-2/3" : "w-4/5"} />
          </div>
        ))}
      </div>
    </SkeletonStatus>
  );
}

function ExplorerContentSkeleton({ label }: { label: string }): React.ReactElement {
  return (
    <SkeletonStatus label={label} className="h-full min-h-0 bg-canvas p-4">
      <div aria-hidden="true" className="flex h-full min-h-0 flex-col gap-4">
        <div className="flex items-center justify-between gap-4">
          <Skeleton shape="text" size="lg" className="w-48" />
          <Skeleton className="h-btn-sm w-24" />
        </div>
        <div className="min-h-0 flex-1 rounded-8 border border-border-subtle bg-sheet p-5">
          <SkeletonText lines={3} className="max-w-2xl" />
          <div className="mt-7 grid gap-4 sm:grid-cols-2">
            {Array.from({ length: 6 }, (_, index) => (
              <div key={index} className="space-y-2 border-b border-border-subtle pb-4">
                <Skeleton shape="text" size="sm" className="w-20" />
                <Skeleton shape="text" size="md" className={index % 2 === 0 ? "w-4/5" : "w-2/3"} />
              </div>
            ))}
          </div>
        </div>
      </div>
    </SkeletonStatus>
  );
}

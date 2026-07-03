import * as React from "react";

import { EmptyState } from "../fragments/EmptyState";
import { LoadingPanel } from "../fragments/LoadingPanel";
import { useUiT } from "../i18n";
import { PrimaryPanePublisher } from "../layouts/primary-pane-context";
import {
  RelationPicker,
  type RelationCreateConfig,
} from "./RelationPicker";
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
  navigatorLabel: string;
  rootPicker: ScopedExplorerRootPicker;
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
 * Shared explorer page shell: root picker + scoped tree are published into the
 * console primary pane, while loading/empty root states own the main surface.
 * Addons keep the facts that are genuinely theirs: tree row projection, drop
 * policy, route transitions, and domain actions.
 */
export function ScopedExplorerPane<
  TRoot,
  TTreeRow extends { id: string },
>({
  loading = false,
  loadingContent,
  emptyContent,
  navigatorLabel,
  rootPicker,
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
      <div
        role="navigation"
        aria-label={navigatorLabel}
        className="flex h-full min-h-0 flex-col gap-2 p-2"
      >
        <RelationPicker
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
        {renderTree(controller)}
        {renderNavigatorFooter?.(controller)}
      </div>
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
    ],
  );
  const primaryPane = hasRoots ? navigator : null;

  if (loading && !hasRoots) {
    return (
      <>
        <PrimaryPanePublisher node={primaryPane} />
        {loadingContent ?? <LoadingPanel />}
      </>
    );
  }
  if (!hasRoots) {
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

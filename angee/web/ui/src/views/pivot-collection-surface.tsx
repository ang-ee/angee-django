import * as React from "react";
import type { ModelMetadata, Row } from "@angee/metadata";

import { useUiT } from "../i18n";
import { ErrorBanner } from "../fragments/ErrorBanner";
import { Button } from "../ui/button";
import type { PagerState } from "../ui/pager";
import type {
  ResourceToolbarGroupOption,
  ResourceToolbarProps,
} from "../toolbars";
import { useArchiveFacetToolbar } from "./archive-facet";
import { PivotViewBody } from "./PivotView";
import { usePivotResourceViewSurface } from "./pivot-view-surface";
import type { ColumnDescriptor } from "./page";
import type { ResourceViewContextValue } from "./resource-view-context";
import type { ResourceViewKind } from "./resource-view-model";
import type { ListEmptyContent, PivotViewSpec } from "./resource-view-types";
import { ResourceListFrame } from "./ResourceListFrame";
import { useResourceToolbarProps } from "./resource-toolbar-props";

// The cross-tabulated collection surface at the `ListView` seam — a component
// boundary beside the client/grouped/server bodies, so the pivot's axis-keyed
// grouped fetch never reorders hooks with the list's `useList` path. It drives
// `usePivotResourceViewSurface`, contributes the row-axis pager and the
// column-axis picker to the shared toolbar, and renders `PivotViewBody`. It
// never calls `useList`.

export interface PivotCollectionSurfaceProps<TRow extends Row = Row> {
  resource: string;
  resourceView: ResourceViewContextValue;
  pivot: PivotViewSpec;
  columns: readonly ColumnDescriptor<TRow>[];
  modelMetadata: ModelMetadata | null;
  /** The page's own filter, merged under the view's URL-owned filter. */
  baseFilter?: Record<string, unknown>;
  availableViews: readonly ResourceViewKind[];
  groupOptions?: readonly ResourceToolbarGroupOption[];
  filterOptions?: ResourceToolbarProps["filterOptions"];
  customFilterFields?: ResourceToolbarProps["customFilterFields"];
  customFilterChips?: ResourceToolbarProps["customFilterChips"];
  favorites?: ResourceToolbarProps["favorites"];
  activeFilterIds?: ResourceToolbarProps["activeFilterIds"];
  filterText?: ResourceToolbarProps["filterText"];
  textFilterField?: string;
  createLabel?: React.ReactNode;
  onCreate?: () => void;
  toolbarActions?: React.ReactNode;
  emptyContent: ListEmptyContent;
  className?: string;
}

export function PivotCollectionSurface<TRow extends Row = Row>({
  resource,
  resourceView,
  pivot,
  columns,
  modelMetadata,
  baseFilter,
  availableViews,
  groupOptions,
  filterOptions,
  customFilterFields,
  customFilterChips,
  favorites,
  activeFilterIds,
  filterText,
  textFilterField,
  createLabel,
  onCreate,
  toolbarActions,
  emptyContent,
  className,
}: PivotCollectionSurfaceProps<TRow>): React.ReactElement {
  const t = useUiT();
  const surface = usePivotResourceViewSurface<TRow>({
    resource,
    columns,
    pivot,
    filter: baseFilter,
    resourceView,
    modelMetadata,
  });
  // The pager windows the outermost row axis, so its unit is axis members.
  const pager = React.useMemo<PagerState>(
    () => ({
      total: surface.rowTotal ?? 0,
      page: resourceView.state.page,
      pageSize: resourceView.state.pageSize,
    }),
    [resourceView.state.page, resourceView.state.pageSize, surface.rowTotal],
  );
  const archive = useArchiveFacetToolbar(resourceView, modelMetadata);
  const toolbar = useResourceToolbarProps({
    resourceView,
    view: "pivot",
    pager,
    pagerTotalUnit: t("pivot.rowUnit"),
    archive,
    groupStack: surface.rowStack,
    group: surface.rowStack[0] ?? null,
    groupOptions,
    columnAxisEnabled: true,
    columnStack: surface.columnStack,
    filterOptions,
    customFilterFields,
    customFilterChips,
    favorites,
    activeFilterIds,
    filterText,
    textFilterField,
    actions: toolbarActions,
    availableViews,
    createLabel,
    onCreate,
  });

  return (
    <ResourceListFrame className={className} toolbar={toolbar}>
      <ErrorBanner
        description={surface.error ? surface.error.message : null}
        actions={
          surface.error ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={surface.refetch}
            >
              {t("calendar.retry")}
            </Button>
          ) : undefined
        }
      />
      <PivotViewBody
        surface={surface}
        resourceView={resourceView}
        emptyContent={emptyContent}
      />
    </ResourceListFrame>
  );
}

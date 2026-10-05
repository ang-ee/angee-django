import * as React from "react";
import type { Row } from "@angee/metadata";

import type { PagerState } from "../../../ui/pager";
import type { ResourceViewContextValue } from "../resource-view-context";
import { DEFAULT_RESOURCE_VIEW_PAGE_SIZE, type ResourceViewKind } from "../resource-view-model";
import { ResourceListFrame } from "../ResourceListFrame";
import { useResourceSearch } from "../search/use-resource-search";
import { useSearchCatalog, type UseSearchCatalogInput } from "../search/catalog";
import type { ResourceToolbarProps } from "../../../toolbars";

// A contributed view kind (`<model>#views`, G-18) at the `ListView` seam: the
// shared toolbar, gated by the kind's declared capabilities, over the kind's own
// body, which reads the collection's filter and state through `useResourceView()`.

/** The kind owns its own paging, so the shared pager stays empty. */
const CONTRIBUTED_PAGER: PagerState = { total: 0, page: 1, pageSize: DEFAULT_RESOURCE_VIEW_PAGE_SIZE };

export interface ContributedViewSurfaceProps<TRow extends Row> {
  searchInput: Omit<UseSearchCatalogInput<TRow>, "rows">;
  resource: string;
  resourceView: ResourceViewContextValue;
  body: React.ComponentType<{ resource: string }>;
  availableViews: readonly ResourceViewKind[];
  createLabel?: React.ReactNode;
  onCreate?: () => void;
  toolbarActions?: React.ReactNode;
  className?: string;
}

export function ContributedViewSurface<TRow extends Row>({
  searchInput,
  resource,
  resourceView,
  body: Body,
  availableViews,
  createLabel,
  onCreate,
  toolbarActions,
  className,
}: ContributedViewSurfaceProps<TRow>): React.ReactElement {
  const catalog = useSearchCatalog({ ...searchInput, resourceView, rows: [] });
  const search = useResourceSearch({ resourceView, catalog, groupingEnabled: false });
  const toolbar: ResourceToolbarProps = {
    search, searchDeclaration: searchInput.search, modelMetadata: searchInput.modelMetadata,
    onViewChange: availableViews.length > 1 ? resourceView.setView : undefined,
    onPageChange: resourceView.setPage, onPageSizeChange: resourceView.setPageSize,
    view: resourceView.state.view,
    pager: CONTRIBUTED_PAGER,
    actions: toolbarActions,
    availableViews,
    createLabel,
    onCreate,
  };
  return (
    <ResourceListFrame className={className} toolbar={toolbar}>
      <Body resource={resource} />
    </ResourceListFrame>
  );
}

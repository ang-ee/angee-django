import * as React from "react";

import type { PagerState } from "../../../ui/pager";
import type { ResourceViewContextValue } from "../resource-view-context";
import { DEFAULT_RESOURCE_VIEW_PAGE_SIZE, type ResourceViewKind } from "../resource-view-model";
import { ResourceListFrame } from "../ResourceListFrame";
import { useResourceToolbarProps } from "../resource-toolbar-props";

// A contributed view kind (`<model>#views`, G-18) at the `ListView` seam: the
// shared toolbar, gated by the kind's declared capabilities, over the kind's own
// body, which reads the collection's filter and state through `useResourceView()`.

/** The kind owns its own paging, so the shared pager stays empty. */
const CONTRIBUTED_PAGER: PagerState = { total: 0, page: 1, pageSize: DEFAULT_RESOURCE_VIEW_PAGE_SIZE };

export interface ContributedViewSurfaceProps {
  resource: string;
  resourceView: ResourceViewContextValue;
  body: React.ComponentType<{ resource: string }>;
  availableViews: readonly ResourceViewKind[];
  createLabel?: React.ReactNode;
  onCreate?: () => void;
  toolbarActions?: React.ReactNode;
  className?: string;
}

export function ContributedViewSurface({
  resource,
  resourceView,
  body: Body,
  availableViews,
  createLabel,
  onCreate,
  toolbarActions,
  className,
}: ContributedViewSurfaceProps): React.ReactElement {
  const toolbar = useResourceToolbarProps({
    resourceView,
    view: resourceView.state.view,
    pager: CONTRIBUTED_PAGER,
    actions: toolbarActions,
    availableViews,
    createLabel,
    onCreate,
  });
  return (
    <ResourceListFrame className={className} toolbar={toolbar}>
      <Body resource={resource} />
    </ResourceListFrame>
  );
}

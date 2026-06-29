import type {
  Row,
  ResourceFilter,
  ResourceOrder,
} from "@angee/resources";
import type {
  ReactNode } from "react";
import type {
  ResourceTypeName,
} from "@angee/resources";
import type {
  ResourceViewDefaultGroups,
  ResourceViewGroup,
  ResourceViewKind,
} from "./resource-view-model";
import type { ButtonVariant } from "../ui/button";

import type {
  ResourceToolbarFilterField,
  ResourceToolbarFilterOption,
  ResourceToolbarGroupOption,
} from "../toolbars";
import type { ResourceListSnapshot } from "./resource-view-surface";
import type { ColumnDescriptor, FacetDescriptor } from "./page";

export interface CardActionContext {
  /** Re-pull the collection backing the board/list surface. */
  refresh: () => void;
}

export interface ListEmptyAction {
  label: ReactNode;
  href?: string;
  onClick?: () => void;
  icon?: ReactNode | string;
  variant?: ButtonVariant;
}

export interface ListEmptyState {
  title: ReactNode;
  description?: ReactNode;
  icon?: ReactNode | string;
  action?: ListEmptyAction;
  actions?: ReactNode;
}

export type ListEmptyContent = ReactNode | ListEmptyState;

export interface ListViewProps<TRow extends Row = Row> {
  /** Model label rendered by this list, e.g. `"notes.Note"`. */
  resource: string;
  /** Columns rendered by the list. */
  columns: readonly ColumnDescriptor<TRow>[];
  /** Extra resource fields selected in addition to the declared columns. */
  fields?: readonly string[];
  /** Base resource filter applied before user-owned view filters. */
  filter?: ResourceFilter<ResourceTypeName>;
  /** Favorite or quick filters shown in the list toolbar. */
  filters?: readonly ResourceToolbarFilterOption[];
  /** Explicit relation facets exposed as quick filters and group-by axes. */
  facets?: readonly FacetDescriptor[];
  /** Fields available to the toolbar's custom filter editor. */
  filterFields?: readonly ResourceToolbarFilterField[];
  /** Fields available to the toolbar's group-by editor. */
  groupOptions?: readonly ResourceToolbarGroupOption[];
  /** Default resource order when the URL-owned data view has no sort. */
  order?: ResourceOrder<ResourceTypeName>;
  /** Initial page size for the URL-owned data view. */
  pageSize?: number;
  /** Initial collection view for the resource list. */
  defaultView?: ResourceViewKind;
  /** Group seeded by the resource list. */
  defaultGroup?: ResourceViewGroup | null;
  /** Per-view group defaults seeded by the resource list. */
  defaultGroups?: ResourceViewDefaultGroups;
  /** Called when the list's create command is invoked. */
  onCreate?: () => void;
  /** Label for the list's create command. */
  createLabel?: ReactNode;
  /** Called when a row is activated. */
  onRowClick?: (row: TRow) => void;
  /** Called whenever the loaded list state changes. */
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
  /** Optional href for a row, used when rows should render as links. */
  rowHref?: (row: TRow) => string;
  /** Controls rendered in the toolbar's leading slot, beside the filter — e.g. a
   * "Connect" button for a list whose rows come from a connect flow. */
  toolbarActions?: ReactNode;
  /** Optional action content rendered in each board card footer. */
  cardActions?: (row: TRow, context: CardActionContext) => ReactNode;
  /** Empty-state content shown when the list has no rows. */
  emptyMessage?: ReactNode;
  /** Structured empty-state content, including an optional action. */
  emptyState?: ListEmptyState;
  /** Class name applied to the collection renderer root. */
  className?: string;
  /** Use a local resource-view state (not URL-synced) even when rendered inside
   * another data view — for an embedded related list on a detail panel. Defaults
   * to inheriting the surrounding route data view (the routed-page behaviour). */
  scope?: "inherit" | "local";
}

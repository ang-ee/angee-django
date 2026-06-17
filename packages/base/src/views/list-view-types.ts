import type { ReactNode } from "react";
import type {
  ResourceTypeName,
  Row,
  UseResourceListOptions,
} from "@angee/sdk";
import type { ButtonVariant } from "../ui/button";

import type {
  DataToolbarFilterField,
  DataToolbarFilterOption,
  DataToolbarGroupOption,
} from "../toolbars";
import type { ListViewState } from "./data-view-surface";
import type { ColumnDescriptor } from "./page";

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

// The lean list contract. Grouping-only props (e.g. defaultGroup) live on
// GroupListViewProps so the flat ListView never advertises grouping it can't do.
export interface ListViewProps<TRow extends Row = Row> {
  /** Model label rendered by this list, e.g. `"notes.Note"`. */
  model: string;
  /** Columns rendered by the list. */
  columns: readonly ColumnDescriptor<TRow>[];
  /** Extra resource fields selected in addition to the declared columns. */
  fields?: readonly string[];
  /** Base resource filter applied before user-owned view filters. */
  filter?: UseResourceListOptions<ResourceTypeName>["filter"];
  /** Favorite or quick filters shown in the list toolbar. */
  filters?: readonly DataToolbarFilterOption[];
  /** Fields available to the toolbar's custom filter editor. */
  filterFields?: readonly DataToolbarFilterField[];
  /** Fields available to the toolbar's group-by editor. */
  groupOptions?: readonly DataToolbarGroupOption[];
  /** Default resource order when the URL-owned data view has no sort. */
  order?: UseResourceListOptions<ResourceTypeName>["order"];
  /** Initial page size for the URL-owned data view. */
  pageSize?: number;
  /** Called when the list's create command is invoked. */
  onCreate?: () => void;
  /** Label for the list's create command. */
  createLabel?: ReactNode;
  /** Called when a row is activated. */
  onRowClick?: (row: TRow) => void;
  /** Called whenever the loaded list state changes. */
  onListStateChange?: (state: ListViewState<TRow>) => void;
  /** Optional href for a row, used when rows should render as links. */
  rowHref?: (row: TRow) => string;
  /** Optional action content rendered in each board card footer. */
  cardActions?: (row: TRow, context: CardActionContext) => ReactNode;
  /** Empty-state content shown when the list has no rows. */
  emptyMessage?: ReactNode;
  /** Structured empty-state content, including an optional action. */
  emptyState?: ListEmptyState;
  /** Class name applied to the collection renderer root. */
  className?: string;
}

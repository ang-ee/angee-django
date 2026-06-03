import type { ReactNode } from "react";
import type {
  ResourceTypeName,
  Row,
  UseResourceListOptions,
} from "@angee/sdk";

import type { ListViewState } from "./data-view-surface";
import type { ColumnDescriptor } from "./page";

// The lean list contract. Grouping-only props (e.g. defaultGroup) live on
// GroupListViewProps so the flat ListView never advertises grouping it can't do.
export interface ListViewProps<TRow extends Row = Row> {
  model: string;
  columns: readonly ColumnDescriptor<TRow>[];
  fields?: readonly string[];
  filter?: UseResourceListOptions<ResourceTypeName>["filter"];
  order?: UseResourceListOptions<ResourceTypeName>["order"];
  pageSize?: number;
  onCreate?: () => void;
  createLabel?: ReactNode;
  onRowClick?: (row: TRow) => void;
  onListStateChange?: (state: ListViewState<TRow>) => void;
  rowHref?: (row: TRow) => string;
  emptyMessage?: ReactNode;
  className?: string;
}

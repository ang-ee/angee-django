import * as React from "react";
import { type ModelMetadata, type Row } from "@angee/metadata";
import { type BaseRecord } from "@refinedev/core";
import { type ColumnDef, type Row as TableRowModel, type Table as TableModel, type VisibilityState } from "@tanstack/react-table";
import { type Virtualizer } from "@tanstack/react-virtual";
import { type AggregateBucket, type AngeeListBatchEntry } from "@angee/refine";
import type { ResourceViewContextValue } from "../resource-view-context";
import { type ResourceListOrder, type ResourceViewFilter, type ResourceViewGroup } from "../resource-view-model";
import { type GroupedListItem, type RowGroup, type VisibleFieldOption } from "../resource-view-list-body";
import type { ColumnDescriptor } from "../../page";
import { type ResolvedBoardLaneSource } from "../resource-view-board-lanes";
import type { BoardCardPlacement } from "../resource-view-types";
export type RowRecord = BaseRecord & Row;
export type ResourceFilterInput = Record<string, unknown>;

export type StringIdRow = Row & { id: string };

export interface ResourceListSnapshot<TRow extends Row = Row> {
  rows: readonly TRow[];
  total: number | undefined;
  page: number;
  pageSize: number;
  pageCount: number | undefined;
  hasNext: boolean;
  hasPrev: boolean;
  fetching: boolean;
  error?: Error | null;
  navigationScope?: ListViewNavigationScope;
}

export interface ListViewNavigationScope {
  filter: ResourceViewFilter | undefined;
  order: ResourceListOrder | undefined;
  page: number;
  pageSize: number;
}

export interface UseResourceViewSurfaceProps<TRow extends Row = Row> {
  resource: string;
  columns: readonly ColumnDescriptor<TRow>[];
  fields?: readonly string[];
  filter?: ResourceFilterInput;
  order?: ResourceListOrder;
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly ResourceViewGroup[];
  defaultExpandedGroups?: "all" | "none";
  laneSource?: ResolvedBoardLaneSource | null;
  enabled?: boolean;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

export interface UseRowsResourceViewSurfaceProps<
  TRow extends StringIdRow = StringIdRow,
> {
  rows: readonly TRow[];
  columns: readonly ColumnDescriptor<TRow>[];
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly ResourceViewGroup[];
  fetching?: boolean;
  error?: Error | null;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

export interface ResourceListResult {
  rows: readonly Row[];
  total: number | undefined;
  pageCount: number | undefined;
  page: number;
  pageSize: number;
  pageInfo: undefined;
  hasNext: boolean;
  hasPrev: boolean;
  setPage: (page: number) => void;
  firstPage: () => void;
  nextPage: () => void;
  prevPage: () => void;
  lastPage: () => void;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

export interface ResourceViewPresentationSurface<TRow extends Row = Row> {
  tableColumns: readonly ColumnDef<TRow>[];
  table: TableModel<TRow>;
  columnVisibility: VisibilityState;
  visibleColumnCount: number;
  visibleFields: readonly VisibleFieldOption[];
  toggleVisibleField: (id: string, visible: boolean) => void;
  rowModels: readonly TableRowModel<TRow>[];
  selectedIds: ReadonlySet<string>;
  tableScrollRef: React.RefObject<HTMLDivElement | null>;
  rowVirtualizer: Virtualizer<HTMLDivElement, Element>;
}

export interface FlatResourceViewPresentationSurface<TRow extends Row = Row>
  extends ResourceViewPresentationSurface<TRow> {
  pageIds: readonly string[];
  allPageSelected: boolean;
  somePageSelected: boolean;
  setPageSelection: (checked: boolean) => void;
  groupedRows: readonly RowGroup<TRow>[];
  boardDragEnabled: boolean;
  boardRankField?: string;
  boardOptimisticPlacementByRowId: ReadonlyMap<string, BoardCardPlacement>;
  onBoardCardMove?: (
    row: TRow,
    laneId: string | null,
    rank?: number,
  ) => void | Promise<void>;
}

interface ResourceViewSurfaceBase<TRow extends Row = Row> {
  list: ResourceListResult;
  listState: ResourceListSnapshot<TRow>;
  rows: readonly TRow[];
  requestedFields: readonly string[];
  mergedFilter: ResourceViewFilter | undefined;
  sortOrder: ResourceListOrder | undefined;
}

export interface ResourceViewSurface<TRow extends Row = Row>
  extends ResourceViewSurfaceBase<TRow>,
    FlatResourceViewPresentationSurface<TRow> {
  kind: "flat";
}

/** Fields owned only by the server-grouped render stream. */
export interface GroupedResourceViewSurface<TRow extends Row = Row>
  extends ResourceViewSurfaceBase<TRow>,
    ResourceViewPresentationSurface<TRow> {
  kind: "grouped";
  /** Grand-total measure footer for the grouped result. */
  footerAggregate: AggregateBucket | null;
  /** Set a server-grouped sub-group/leaf scope's page. */
  setScopePage: (key: string, page: number) => void;
  /** The windowed server-grouped render stream. */
  groupedItems: readonly GroupedListItem<TRow>[];
  /** Server `_groups` bucket expansion keys. */
  expandedKeys: ReadonlySet<string>;
  toggleGroup: (key: string) => void;
}

export const EMPTY_ARRAY = [] as const;
export const EMPTY_BOARD_PLACEMENTS: ReadonlyMap<string, BoardCardPlacement> = new Map();
export const EMPTY_SELECTED_IDS: ReadonlySet<string> = new Set();
export const EMPTY_EXPANDED_KEYS: ReadonlySet<string> = new Set();
export const EMPTY_LEAF_RESULTS: ReadonlyMap<string, AngeeListBatchEntry> = new Map();

export interface RowsResourceViewSurface<TRow extends StringIdRow = StringIdRow>
  extends FlatResourceViewPresentationSurface<TRow> {
  kind: "flat";
  list: ResourceListSnapshot<TRow>;
  listState: ResourceListSnapshot<TRow>;
  rows: readonly TRow[];
  sourceRows: readonly TRow[];
}

export interface ResourceRowsSnapshotSource {
  rows: readonly Row[];
  total: number | undefined;
  page: number;
  pageSize: number;
  pageCount: number | undefined;
  hasNext: boolean;
  hasPrev: boolean;
  fetching: boolean;
  error: Error | null;
}

export interface UseResourceRowsSnapshotOptions<TRow extends Row> {
  navigation?: Pick<ListViewNavigationScope, "filter" | "order">;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

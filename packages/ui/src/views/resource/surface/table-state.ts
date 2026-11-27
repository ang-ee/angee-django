import * as React from "react";
import { type QueryFilter, type ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
import { stableKey } from "@angee/refine";
import { functionalUpdate, type ColumnDef, type GroupingState, type OnChangeFn, type PaginationState, type RowSelectionState, type SortingState, type Table, type TableMeta, type VisibilityState } from "@tanstack/react-table";
import { filterForResourceTextSearch, queryForColumns } from "../resource-query";
import { errorFromUnknown } from "../../../data/errors";
import type { ResourceViewContextValue } from "../resource-view-context";
import { useArchiveScopeFilter } from "./archive-scope";
import { Filter, type ResourceListOrder, type ResourceViewGroup } from "../resource-view-model";
import { buildColumns, tableGroupAxes, withQueryOnlyColumnsHidden } from "../resource-view-list-body";
import type { ColumnDescriptor } from "../../page";
import { type ResolvedBoardLaneSource } from "../resource-view-board-lanes";
import { normalisePageSize } from "../page-size";
import { defaultResourceOrder, leafTableRows, requestedFieldPaths } from "../resource-view-codecs";
import { querySortFromSorting } from "../model/state";
import { navigationScopeKey } from "../record-navigation-context";
import type { ListRecordFacts, ListViewNavigationScope, ResourceFilterInput, ResourceListResult, ResourceListSnapshot, ResourceRowsSnapshotSource, UseResourceRowsSnapshotOptions } from "./types";
import { useLatestRef } from "../../../lib/use-latest-ref";
import { useValueStable } from "../../../lib/use-value-stable";

export function useResourceRowsSnapshot<TRow extends Row = Row>(
  list: ResourceRowsSnapshotSource,
  options: UseResourceRowsSnapshotOptions<TRow> = {},
): ResourceListSnapshot<TRow> {
  const { navigation, onListStateChange } = options;
  const rows = list.rows as readonly TRow[];
  const navigationScope = React.useMemo<ListViewNavigationScope | undefined>(
    () => navigation
      ? {
          ...navigation,
          page: list.page,
          pageSize: list.pageSize,
        }
      : undefined,
    [list.page, list.pageSize, navigation?.filter, navigation?.order],
  );
  const snapshot = React.useMemo<ResourceListSnapshot<TRow>>(
    () => ({
      rows,
      total: list.total,
      page: list.page,
      pageSize: list.pageSize,
      pageCount: list.pageCount,
      hasNext: list.hasNext,
      hasPrev: list.hasPrev,
      ...(list.records ? { records: list.records } : {}),
      fetching: list.fetching,
      error: list.error,
      ...(navigationScope ? { navigationScope } : {}),
    }),
    [
      rows,
      list.total,
      list.page,
      list.pageSize,
      list.pageCount,
      list.hasNext,
      list.hasPrev,
      list.records,
      list.fetching,
      list.error,
      navigationScope,
    ],
  );
  // Collection consumers publish the snapshot back into parent state. Some
  // table projections allocate an equivalent rows array after that parent
  // render; depending on raw object identity turns the publication into an
  // effect -> setState -> render loop. The query facts and row values are the
  // snapshot identity, so retain one object until those facts actually change, the order's
  // priority among them (`navigationScopeKey`).
  const snapshotKey = stableKey([snapshot, navigationScopeKey(navigationScope)]);
  const stableSnapshot = React.useMemo(() => snapshot, [snapshotKey]);
  const onListStateChangeRef = useLatestRef(onListStateChange);
  React.useEffect(() => {
    onListStateChangeRef.current?.(stableSnapshot);
  }, [onListStateChangeRef, stableSnapshot]);
  return stableSnapshot;
}

export function listResultFromPageState<TRow extends Row>({
  resourceView,
  error,
  fetching,
  refetch,
  rows,
  total,
  page = (resourceView.state.pagination.pageIndex + 1),
  pageSize = resourceView.state.pagination.pageSize,
  pageCount = total === undefined
    ? undefined
    : Math.max(1, Math.ceil(total / pageSize)),
  hasNext = pageCount !== undefined && page < pageCount,
  pageUnit = "records",
}: {
  resourceView: ResourceViewContextValue;
  error: unknown;
  fetching: boolean;
  refetch: () => void;
  rows: readonly TRow[];
  total: number | undefined;
  page?: number;
  pageSize?: number;
  pageCount?: number | undefined;
  hasNext?: boolean;
  pageUnit?: ResourceListResult["pageUnit"];
}): ResourceListResult {
  return {
    rows,
    total,
    pageCount,
    page,
    pageSize,
    pageInfo: undefined,
    hasNext,
    pageUnit,
    hasPrev: page > 1,
    setPage: resourceView.setPage,
    firstPage: () => resourceView.setPage(1),
    nextPage: () =>
      resourceView.setPage(pageCount ? Math.min(page + 1, pageCount) : page + 1),
    prevPage: () => resourceView.setPage(Math.max(1, page - 1)),
    lastPage: () => {
      if (pageCount) resourceView.setPage(pageCount);
    },
    fetching,
    error: errorFromUnknown(error),
    refetch,
  };
}

export interface UseResourceViewQueryFactsProps<TRow extends Row> {
  columns: readonly ColumnDescriptor<TRow>[];
  fields?: readonly string[];
  filter?: ResourceFilterInput;
  order?: ResourceListOrder;
  resourceView: ResourceViewContextValue;
  modelMetadata: ModelMetadata | null | undefined;
  laneSource?: ResolvedBoardLaneSource | null;
  groupStack?: readonly ResourceViewGroup[];
  /** Client row models sort only from live view state after fetching all rows. */
  includeDeclaredOrder?: boolean;
}

/** Shared request facts derived by every server/client resource surface. */
export function useResourceViewQueryFacts<TRow extends Row>({
  columns,
  fields,
  filter,
  order,
  resourceView,
  modelMetadata,
  laneSource,
  groupStack = resourceView.state.groupStack,
  includeDeclaredOrder = true,
}: UseResourceViewQueryFactsProps<TRow>): {
  requestedFields: readonly string[];
  mergedFilter: QueryFilter;
  sortOrder: ResourceListOrder | undefined;
} {
  const requestedFields = React.useMemo(
    () => requestedFieldPaths(columns, fields, modelMetadata, laneSource, groupStack),
    [columns, fields, laneSource, modelMetadata, groupStack],
  );
  const unscopedFilter = React.useMemo(
    () => filterForResourceTextSearch(
      modelMetadata,
      Filter.combineOptional(filter, Filter.combineOptional(resourceView.baseFilter, resourceView.state.filter)),
    ),
    [filter, resourceView.state.filter, resourceView.baseFilter, modelMetadata],
  );
  const mergedFilter = useArchiveScopeFilter(modelMetadata?.resource, unscopedFilter);
  const sortOrder = React.useMemo(
    () => {
      const sorting = resourceView.state.sorting;
      if (sorting !== undefined) {
        return Object.fromEntries(sorting.map(({ id, desc }) => [id, desc ? "DESC" : "ASC"]));
      }
      const declaredOrder = includeDeclaredOrder ? order ?? defaultResourceOrder(modelMetadata) : undefined;
      return declaredOrder;
    },
    [includeDeclaredOrder, resourceView.state.sorting, modelMetadata, order],
  );
  return { requestedFields, mergedFilter, sortOrder };
}

/** Shared URL-to-TanStack state used by every resource-view surface. */
export function useResourceViewTableState<TRow extends Row>({
  columns,
  resourceView,
  modelMetadata,
  groupStack,
  sortOrder,
  maxPageSize,
  clientOperations = false,
  query,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  resourceView: ResourceViewContextValue;
  modelMetadata: ModelMetadata | null | undefined;
  groupStack: readonly ResourceViewGroup[];
  sortOrder?: ResourceListOrder;
  maxPageSize?: number;
  clientOperations?: boolean;
  query?: ResourceQuery;
}): {
  tableColumns: readonly ColumnDef<TRow>[];
  columnVisibility: VisibilityState;
  effectiveColumnVisibility: VisibilityState;
  setColumnVisibility: OnChangeFn<VisibilityState>;
  pagination: PaginationState;
  /** The table's sorting: each group axis's key, ascending (its column carries the groups' direction), then the list's order without the keys an axis shares. */
  sorting: SortingState;
  grouping: GroupingState;
  /** The table's `meta`, which each list surface sets (resource, presentation, grouped): the list's group axes and its order, read by its headers. */
  meta: TableMeta<TRow>;
  rowSelection: RowSelectionState;
  handlePaginationChange: OnChangeFn<PaginationState>;
  handleSortingChange: OnChangeFn<SortingState>;
  handleRowSelectionChange: (updater: Parameters<OnChangeFn<RowSelectionState>>[0], table: Table<TRow>) => void;
} {
  const groupAxes = React.useMemo(() => tableGroupAxes(groupStack, modelMetadata, columns, query), [groupStack, modelMetadata, columns, query]);
  // The list's whole order, as its view holds it or inherits it: what it stores, puts in its URL
  // and saves in a view, and what its headers read (`TableMeta.sorting`).
  const order = React.useMemo(() => {
    const declared = resourceView.state.sorting && querySortFromSorting(resourceView.state.sorting);
    return (query ?? queryForColumns(columns, modelMetadata, groupStack)).sortFrom(declared ?? sortOrder);
  }, [query, columns, modelMetadata, groupStack, resourceView.state.sorting, sortOrder]);
  // The direction each axis's groups go in under the order (`GroupAxis.direction`), which their
  // columns' comparators carry; the same array while none turns, so the columns are built again
  // only when a group's direction turns.
  const directions = useValueStable(groupAxes.map((axis) => axis.direction(order)));
  const tableColumns = React.useMemo(
    () =>
      buildColumns(columns, { axes: groupAxes, directions, metadata: modelMetadata, clientOperations, query }),
    [columns, groupAxes, directions, modelMetadata, clientOperations, query],
  );
  const columnVisibility = React.useMemo(() => ({
    ...Object.fromEntries(columns.map((column) => [column.id ?? column.field, !column.hiddenByDefault])),
    ...resourceView.state.columnVisibility,
    ...Object.fromEntries(columns.filter((column) => column.hideable === false)
      .map((column) => [column.id ?? column.field, true])),
  }), [columns, resourceView.state.columnVisibility]);
  const setColumnVisibility = resourceView.setColumnVisibility;
  const effectiveColumnVisibility = React.useMemo(
    () => withQueryOnlyColumnsHidden(tableColumns, columnVisibility),
    [tableColumns, columnVisibility],
  );
  const statePagination = resourceView.state.pagination;
  const boundedPageSize = maxPageSize === undefined
    ? statePagination.pageSize
    : Math.min(maxPageSize, statePagination.pageSize);
  const pagination = React.useMemo(
    () => boundedPageSize === statePagination.pageSize
      ? statePagination
      : { ...statePagination, pageSize: boundedPageSize },
    [boundedPageSize, statePagination],
  );
  React.useEffect(() => {
    if (pagination !== statePagination) resourceView.setPagination(pagination);
  }, [pagination, resourceView.setPagination, statePagination]);
  const listSorting = React.useMemo<SortingState>(
    () => order.map(({ field, direction }) => ({ id: field, desc: direction === "DESC" })),
    [order],
  );
  // What the table sorts by: each group axis's key, ascending, since its column's comparator
  // carries the groups' direction (`buildColumns`), so the groups follow their own axis, then the
  // order's keys no axis shares, which each group's records follow. A turned direction is a new
  // order, so a new sorting, which the table sorts again with the columns built for it. Nothing
  // reads an axis key's `desc` here: the headers, a press and the Order select read the order
  // (`TableMeta.sorting`), and a press reaches the table's own toggle only on a column no axis
  // shares, whose key this hands as the order holds it.
  const sorting = React.useMemo<SortingState>(() => [
    ...groupAxes.map((axis) => ({ id: axis.id, desc: false })),
    ...listSorting.filter(({ id }) => !groupAxes.some((axis) => axis.shares(id))),
  ], [groupAxes, listSorting]);
  const grouping = React.useMemo(() => groupAxes.map((axis) => axis.id), [groupAxes]);
  const meta = React.useMemo<TableMeta<TRow>>(() => ({ groupAxes, sorting: listSorting }), [groupAxes, listSorting]);
  const rowSelection = resourceView.state.rowSelection;
  const handlePaginationChange = React.useCallback<OnChangeFn<PaginationState>>(
    (updater) => {
      const next = functionalUpdate(updater, pagination);
      const pageSize = normalisePageSize(next.pageSize);
      resourceView.setPagination({
        ...next,
        pageSize: maxPageSize === undefined ? pageSize : Math.min(maxPageSize, pageSize),
      });
    },
    [maxPageSize, pagination, resourceView.setPagination],
  );
  const handleRowSelectionChange = React.useCallback(
    (updater: Parameters<OnChangeFn<RowSelectionState>>[0], table: Table<TRow>) =>
      resourceView.setRowSelection(updater, (id) => table.getCoreRowModel().rowsById[id]?.original),
    [resourceView.setRowSelection],
  );
  // A change applies to the list's order, never to the keys the table is handed for its groups.
  const handleSortingChange = React.useCallback<OnChangeFn<SortingState>>(
    (updater) => resourceView.setSorting(functionalUpdate(updater, listSorting)),
    [resourceView.setSorting, listSorting],
  );

  return {
    tableColumns,
    columnVisibility,
    effectiveColumnVisibility,
    setColumnVisibility,
    pagination,
    sorting,
    grouping,
    meta,
    rowSelection,
    handlePaginationChange,
    handleSortingChange,
    handleRowSelectionChange,
  };
}


/**
 * Keep the view's page inside the settled page count: a page beyond it (a stale URL, a
 * set that shrank) moves to the last page. Nothing moves while a read is in flight: the
 * count is not known yet, and clamping then would discard the page the URL asked for.
 */
export function useSettledPageClamp(
  resourceView: ResourceViewContextValue,
  pageCount: number | undefined,
  settled: boolean,
): void {
  const page = resourceView.state.pagination.pageIndex + 1;
  const { setPage } = resourceView;
  React.useEffect(() => {
    if (!settled || pageCount === undefined || page <= pageCount) return;
    setPage(pageCount);
  }, [page, pageCount, settled, setPage]);
}

/**
 * A grouping client table pages its root groups; the record pager needs the records
 * behind those pages and how many precede the current one. Absent when pages are records.
 */
export function tableRecordFacts<TRow extends Row>(table: Table<TRow>): ListRecordFacts | undefined {
  if (table.options.manualPagination || table.getState().grouping.length === 0) return undefined;
  const { pageIndex, pageSize } = table.getState().pagination;
  return {
    total: table.getFilteredRowModel().rows.length,
    before: leafTableRows(table.getPrePaginationRowModel().rows.slice(0, pageIndex * pageSize)).length,
  };
}

/** Presentation projection over the native table/query owners; no paging state. */
export function listResultFromTable<TRow extends Row>(
  table: Table<TRow>,
  { rows, total, fetching, error, refetch }: Pick<ResourceListResult, "rows" | "total" | "fetching" | "refetch"> & { error: unknown },
): ResourceListResult {
  const { pageIndex, pageSize } = table.getState().pagination;
  const records = tableRecordFacts(table);
  return {
    rows, total, fetching, error: errorFromUnknown(error), refetch,
    page: pageIndex + 1,
    pageSize,
    pageInfo: undefined,
    pageCount: total === undefined ? undefined : Math.max(1, table.getPageCount()),
    hasNext: total !== undefined && table.getCanNextPage(),
    hasPrev: table.getCanPreviousPage(),
    pageUnit: records ? "groups" : "records",
    ...(records ? { records } : {}),
    setPage: (page) => table.setPageIndex(page - 1),
    firstPage: table.firstPage,
    nextPage: table.nextPage,
    prevPage: table.previousPage,
    lastPage: () => { if (total !== undefined) table.lastPage(); },
  };
}

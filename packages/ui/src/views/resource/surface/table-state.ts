import * as React from "react";
import { stableSerialize } from "@angee/refine";
import { type ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
import { functionalUpdate, type ColumnDef, type OnChangeFn, type PaginationState, type RowSelectionState, type SortingState, type Table, type VisibilityState } from "@tanstack/react-table";
import { queryForColumns } from "../resource-query";
import { errorFromUnknown } from "../../../data/errors";
import type { ResourceViewContextValue } from "../resource-view-context";
import { Filter, type ResourceListOrder, type ResourceViewFilter, type ResourceViewGroup } from "../resource-view-model";
import { buildColumns, withQueryOnlyColumnsHidden } from "../resource-view-list-body";
import type { ColumnDescriptor } from "../../page";
import { type ResolvedBoardLaneSource } from "../resource-view-board-lanes";
import { normalisePageSize } from "../page-size";
import { defaultResourceOrder, groupingStateFromResourceGroups, requestedFieldPaths } from "../resource-view-codecs";
import type { ListViewNavigationScope, ResourceFilterInput, ResourceListResult, ResourceListSnapshot, ResourceRowsSnapshotSource, UseResourceRowsSnapshotOptions } from "./types";
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
      list.fetching,
      list.error,
      navigationScope,
    ],
  );
  // The effect keys on the snapshot's CONTENT, not on its identity. Keyed on
  // identity it re-ran on every render -- the memo rebuilds when `rows` or the
  // list object is new, and `onListStateChange` is itself rebuilt whenever one of
  // its own dependencies churns -- so a board load ran this effect ~60 times for
  // a list whose contents never changed. Each run called two setters, and React
  // can only skip scheduling a same-value update while the fiber has no pending
  // lane; once the first update lands, every later one is scheduled, renders,
  // returns the same value, and still counts as a nested update. That is the
  // "Maximum update depth exceeded" the board threw on load.
  //
  // Row identity is not content: rows are refetched objects, so the ids are what
  // decide whether the consumer needs telling.
  const snapshotKey = stableSerialize({
    rowIds: rows.map((row) => (row as { id?: unknown }).id ?? null),
    total: list.total,
    page: list.page,
    pageSize: list.pageSize,
    pageCount: list.pageCount,
    hasNext: list.hasNext,
    hasPrev: list.hasPrev,
    fetching: list.fetching,
    error: list.error ? String(list.error.message ?? list.error) : null,
    navigationScope: navigationScope ?? null,
  });
  const latest = React.useRef({ snapshot, onListStateChange });
  latest.current = { snapshot, onListStateChange };
  React.useEffect(() => {
    latest.current.onListStateChange?.(latest.current.snapshot);
  }, [snapshotKey]);
  return snapshot;
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
}): ResourceListResult {
  return {
    rows,
    total,
    pageCount,
    page,
    pageSize,
    pageInfo: undefined,
    hasNext,
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
  mergedFilter: ResourceViewFilter | undefined;
  sortOrder: ResourceListOrder | undefined;
} {
  const requestedFields = React.useMemo(
    () => requestedFieldPaths(columns, fields, modelMetadata, laneSource, groupStack),
    [columns, fields, laneSource, modelMetadata, groupStack],
  );
  const mergedFilter = React.useMemo(
    () => Filter.combineOptional(filter, resourceView.state.filter),
    [resourceView.state.filter, filter],
  );
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
  sorting: SortingState;
  grouping: ReturnType<typeof groupingStateFromResourceGroups>;
  rowSelection: RowSelectionState;
  handlePaginationChange: OnChangeFn<PaginationState>;
  handleSortingChange: OnChangeFn<SortingState>;
  handleRowSelectionChange: OnChangeFn<RowSelectionState>;
} {
  const tableColumns = React.useMemo(
    () =>
      buildColumns(columns, { groupStack, metadata: modelMetadata, clientOperations, query }),
    [columns, groupStack, modelMetadata, clientOperations, query],
  );
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>({});
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
  const sorting = React.useMemo<SortingState>(() => {
    const declared = resourceView.state.sorting?.map(({ id, desc }) => ({ field: id, direction: desc ? "DESC" : "ASC" }));
    return (query ?? queryForColumns(columns, modelMetadata, groupStack)).sortFrom(declared ?? sortOrder)
      .map(({ field, direction }) => ({ id: field, desc: direction === "DESC" }));
  }, [query, columns, modelMetadata, groupStack, resourceView.state.sorting, sortOrder]);
  const grouping = React.useMemo(() => groupingStateFromResourceGroups(groupStack, modelMetadata, columns, query), [groupStack, modelMetadata, columns, query]);
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
  const handleRowSelectionChange = resourceView.setRowSelection;
  const handleSortingChange = React.useCallback<OnChangeFn<SortingState>>(
    (updater) => resourceView.setSorting(functionalUpdate(updater, sorting)),
    [resourceView.setSorting, sorting],
  );

  return {
    tableColumns,
    columnVisibility,
    effectiveColumnVisibility,
    setColumnVisibility,
    pagination,
    sorting,
    grouping,
    rowSelection,
    handlePaginationChange,
    handleSortingChange,
    handleRowSelectionChange,
  };
}


/** Presentation projection over the native table/query owners; no paging state. */
export function listResultFromTable<TRow extends Row>(
  table: Table<TRow>,
  { rows, total, fetching, error, refetch }: Pick<ResourceListResult, "rows" | "total" | "fetching" | "refetch"> & { error: unknown },
): ResourceListResult {
  const { pageIndex, pageSize } = table.getState().pagination;
  return {
    rows, total, fetching, error: errorFromUnknown(error), refetch,
    page: pageIndex + 1,
    pageSize,
    pageInfo: undefined,
    pageCount: total === undefined ? undefined : Math.max(1, table.getPageCount()),
    hasNext: total !== undefined && table.getCanNextPage(),
    hasPrev: table.getCanPreviousPage(),
    setPage: (page) => table.setPageIndex(page - 1),
    firstPage: table.firstPage,
    nextPage: table.nextPage,
    prevPage: table.previousPage,
    lastPage: () => { if (total !== undefined) table.lastPage(); },
  };
}

import * as React from "react";
import { type ModelMetadata, type Row } from "@angee/metadata";
import { functionalUpdate, type ColumnDef, type OnChangeFn, type PaginationState, type RowSelectionState, type SortingState, type VisibilityState } from "@tanstack/react-table";
import { refineSortersFromAngeeOrder } from "@angee/refine";
import { errorFromUnknown } from "../../../data/errors";
import type { ResourceViewContextValue } from "../resource-view-context";
import { Filter, type ResourceListOrder, type ResourceViewFilter, type ResourceViewGroup } from "../resource-view-model";
import { buildColumns, withGroupingOnlyColumnsHidden } from "../resource-view-list-body";
import type { ColumnDescriptor } from "../../page";
import { type ResolvedBoardLaneSource } from "../resource-view-board-lanes";
import { defaultResourceOrder, groupingStateFromResourceGroups, idsFromRowSelectionState, requestedFieldPaths, rowSelectionStateFromIds, sortingStateFromResourceSort } from "../resource-view-codecs";
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
  React.useEffect(() => {
    onListStateChange?.(snapshot);
  }, [onListStateChange, snapshot]);
  return snapshot;
}

export function listResultFromPageState<TRow extends Row>({
  resourceView,
  error,
  fetching,
  refetch,
  rows,
  total,
  page = resourceView.state.page,
  pageSize = resourceView.state.pageSize,
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
  includeDeclaredOrder = true,
}: UseResourceViewQueryFactsProps<TRow>): {
  requestedFields: readonly string[];
  mergedFilter: ResourceViewFilter | undefined;
  sortOrder: ResourceListOrder | undefined;
} {
  const requestedFields = React.useMemo(
    () => requestedFieldPaths(columns, fields, modelMetadata, laneSource),
    [columns, fields, laneSource, modelMetadata],
  );
  const mergedFilter = React.useMemo(
    () => Filter.combineOptional(filter, resourceView.state.filter),
    [resourceView.state.filter, filter],
  );
  const sortOrder = React.useMemo(
    () =>
      resourceView.state.resourceOrder()
      ?? (includeDeclaredOrder
        ? order ?? defaultResourceOrder(modelMetadata)
        : undefined),
    [includeDeclaredOrder, resourceView.state.sort, modelMetadata, order],
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
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  resourceView: ResourceViewContextValue;
  modelMetadata: ModelMetadata | null | undefined;
  groupStack: readonly ResourceViewGroup[];
  sortOrder?: ResourceListOrder;
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
      buildColumns(columns, {
        sort: resourceView.state.sort,
        setSort: resourceView.setSort,
      }, {
        groupStack,
        metadata: modelMetadata,
      }),
    [columns, groupStack, modelMetadata, resourceView.state.sort, resourceView.setSort],
  );
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>({});
  const effectiveColumnVisibility = React.useMemo(
    () => withGroupingOnlyColumnsHidden(tableColumns, columnVisibility),
    [tableColumns, columnVisibility],
  );
  const pagination = React.useMemo<PaginationState>(
    () => ({
      pageIndex: Math.max(0, resourceView.state.page - 1),
      pageSize: resourceView.state.pageSize,
    }),
    [resourceView.state.page, resourceView.state.pageSize],
  );
  const sorting = React.useMemo<SortingState>(() => {
    if (!sortOrder) return sortingStateFromResourceSort(resourceView.state.sort);
    return (refineSortersFromAngeeOrder(sortOrder) ?? []).map((sorter) => ({
      id: sorter.field,
      desc: sorter.order === "desc",
    }));
  }, [resourceView.state.sort, sortOrder]);
  const grouping = React.useMemo(
    () => groupingStateFromResourceGroups(groupStack),
    [groupStack],
  );
  const rowSelection = React.useMemo(
    () => rowSelectionStateFromIds(resourceView.state.selectedIds),
    [resourceView.state.selectedIds],
  );
  const handlePaginationChange = React.useCallback<OnChangeFn<PaginationState>>(
    (updater) => {
      const next = functionalUpdate(updater, pagination);
      if (next.pageSize !== resourceView.state.pageSize) {
        resourceView.setPageSize(next.pageSize);
      }
      const nextPage = next.pageIndex + 1;
      if (nextPage !== resourceView.state.page) resourceView.setPage(nextPage);
    },
    [pagination, resourceView],
  );
  const handleSortingChange = React.useCallback<OnChangeFn<SortingState>>(
    (updater) => {
      const [next] = functionalUpdate(updater, sorting);
      resourceView.setSort(
        next ? { field: next.id, dir: next.desc ? "desc" : "asc" } : null,
      );
    },
    [resourceView, sorting],
  );
  const handleRowSelectionChange = React.useCallback<OnChangeFn<RowSelectionState>>(
    (updater) => {
      resourceView.setSelectedIds(
        idsFromRowSelectionState(functionalUpdate(updater, rowSelection)),
      );
    },
    [resourceView, rowSelection],
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

import * as React from "react";
import { type Row } from "@angee/metadata";
import { MAX_PAGE_SIZE } from "@angee/refine";
import { useReactTable, getCoreRowModel, getExpandedRowModel, getGroupedRowModel, type ColumnDef, type ExpandedState } from "@tanstack/react-table";
import { useResourceListQuery } from "./resource-list-query";
import { useBoardLaneState } from "../resource-view-board-lanes";
import { modelRowId } from "../resource-view-codecs";
import { useResourceViewPresentationSurfaceFromTable } from "./presentation";
import { listResultFromTable, useResourceRowsSnapshot, useResourceViewQueryFacts, useResourceViewTableState } from "./table-state";
import type { ResourceViewSurface, UseResourceViewSurfaceProps } from "./types";
import { useCollectionQueryBatch } from "../collection-source";
import { EMPTY_ARRAY } from "./types";
export function useResourceViewSurface<TRow extends Row = Row>({
  source,
  columns,
  fields,
  filter,
  order,
  resourceView,
  modelMetadata = null,
  groupStack,
  laneSource,
  enabled = true,
  onListStateChange,
}: UseResourceViewSurfaceProps<TRow>): ResourceViewSurface<TRow> {
  const { requestedFields, mergedFilter, sortOrder } =
    useResourceViewQueryFacts({
      columns,
      fields,
      filter,
      order,
      resourceView,
      modelMetadata,
      laneSource,
      groupStack,
    });
  const rowGroupStack = groupStack ?? resourceView.state.groupStack;
  const [expanded, setExpanded] = React.useState<ExpandedState>({});
  const dataResource = modelMetadata?.resource ?? null;
  const tableState = useResourceViewTableState({
    query: source?.query,
    columns,
    resourceView,
    modelMetadata,
    groupStack:
      laneSource && resourceView.state.view === "board" ? [] : rowGroupStack,
    sortOrder,
    maxPageSize: MAX_PAGE_SIZE,
  });
  const {
    tableColumns,
    columnVisibility,
    effectiveColumnVisibility,
    setColumnVisibility,
    pagination: paginationState,
    sorting: sortingState,
    grouping,
    rowSelection,
    handlePaginationChange,
    handleSortingChange,
    handleRowSelectionChange,
  } = tableState;
  const active = enabled && Boolean(source || dataResource);
  const listQuery = useResourceListQuery({
    resource: source ? null : dataResource,
    scope: {
      filter: mergedFilter,
      order: sortOrder,
      page: paginationState.pageIndex + 1,
      pageSize: paginationState.pageSize,
    },
    fields: requestedFields,
    enabled: active && !source,
  });
  const sourceRequests = React.useMemo(
    () =>
      active
        ? [
            {
              key: "root",
              filter: mergedFilter,
              order: sortOrder,
              page: paginationState.pageIndex + 1,
              pageSize: paginationState.pageSize,
            },
          ]
        : [],
    [
      active,
      mergedFilter,
      sortOrder,
      paginationState.pageIndex,
      paginationState.pageSize,
    ],
  );
  const authored = useCollectionQueryBatch(source?.rows, sourceRequests).get(
    "root",
  );
  const rows = (
    source ? authored?.data?.rows ?? EMPTY_ARRAY : listQuery.result.data
  ) as TRow[];
  const total = source ? authored?.data?.total : listQuery.result.total;
  const fetching = source
    ? authored?.fetching ?? active
    : listQuery.query.isFetching;
  const error = source ? authored?.error : listQuery.query.error;
  const refetchRows = source ? authored?.refetch : listQuery.query.refetch;
  const settled = source
    ? authored?.data !== undefined && !error
    : listQuery.query.isSuccess && !listQuery.query.isPlaceholderData;
  React.useEffect(() => {
    if (!active || !settled || fetching || total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(total / paginationState.pageSize));
    if (paginationState.pageIndex >= lastPage) resourceView.setPage(lastPage);
  }, [
    active,
    settled,
    fetching,
    total,
    paginationState.pageIndex,
    paginationState.pageSize,
    resourceView.setPage,
  ]);
  const table = useReactTable<TRow>({
    data: rows,
    columns: tableColumns as ColumnDef<TRow>[],
    rowCount: total,
    pageCount: total === undefined ? -1 : undefined,
    manualPagination: true,
    manualSorting: true,
    manualFiltering: true,
    enableMultiSort: false,
    getCoreRowModel: getCoreRowModel(),
    state: {
      columnVisibility: effectiveColumnVisibility,
      expanded,
      grouping,
      pagination: paginationState,
      rowSelection,
      sorting: sortingState,
    },
    onColumnVisibilityChange: setColumnVisibility,
    onExpandedChange: setExpanded,
    onPaginationChange: handlePaginationChange,
    onRowSelectionChange: handleRowSelectionChange,
    onSortingChange: handleSortingChange,
    getRowId: (row, index) => modelRowId(row, index, dataResource),
    enableRowSelection: (row) => !row.getIsGrouped(),
    getGroupedRowModel: getGroupedRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    autoResetPageIndex: false,
    autoResetExpanded: false,
  });
  const boardLaneState = useBoardLaneState<TRow>({
    laneSource,
    modelMetadata,
    rows,
    enabled: active && resourceView.state.view === "board",
    refetchRows: refetchRows ?? (() => {}),
  });
  const list = React.useMemo(
    () => ({
      ...listResultFromTable(table, {
        error: error ?? null,
        fetching: fetching || boardLaneState.fetching,
        refetch: () => {
          refetchRows?.();
        },
        rows,
        total,
      }),
      summary: authored?.data?.summary,
    }),
    [
      authored?.data?.summary,
      boardLaneState.fetching,
      table,
      rows,
      total,
      error,
      fetching,
      refetchRows,
    ],
  );
  const listState = useResourceRowsSnapshot<TRow>(list, {
    navigation: { filter: mergedFilter, order: sortOrder },
    onListStateChange,
  });

  const presentation = useResourceViewPresentationSurfaceFromTable({
    rows,
    table,
    columnVisibility,
    resourceView,
    groupStack,
    boardLaneState,
  });

  return {
    kind: "flat",
    list,
    listState,
    rows,
    requestedFields,
    mergedFilter,
    sortOrder,
    ...presentation,
  };
}

/** Max rows a client resource fetches in one page; warn (never truncate silently) at the cap. */
export const CLIENT_ROW_MODEL_FETCH_CAP = 1000;

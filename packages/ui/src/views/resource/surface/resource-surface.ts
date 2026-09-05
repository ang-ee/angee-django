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
export function useResourceViewSurface<TRow extends Row = Row>({
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
  const { requestedFields, mergedFilter, sortOrder } = useResourceViewQueryFacts({
    columns,
    fields,
    filter,
    order,
    resourceView,
    modelMetadata,
    laneSource,
  });
  const rowGroupStack = groupStack ?? resourceView.state.groupStack;
  const [expanded, setExpanded] = React.useState<ExpandedState>({});
  const dataResource = modelMetadata?.resource ?? null;
  const tableState = useResourceViewTableState({
    columns,
    resourceView,
    modelMetadata,
    groupStack: rowGroupStack,
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
  const active = enabled && Boolean(dataResource);
  const listQuery = useResourceListQuery({
    resource: dataResource,
    scope: {
      filter: mergedFilter, order: sortOrder,
      page: paginationState.pageIndex + 1, pageSize: paginationState.pageSize,
    },
    fields: requestedFields,
    enabled: active,
  });
  const rows = listQuery.result.data as TRow[];
  const total = listQuery.result.total;
  React.useEffect(() => {
    if (!active || !listQuery.query.isSuccess || listQuery.query.isFetching
      || listQuery.query.isPlaceholderData || total === undefined) return;
    const lastPage = Math.max(1, Math.ceil(total / paginationState.pageSize));
    if (paginationState.pageIndex >= lastPage) resourceView.setPage(lastPage);
  }, [active, listQuery.query.isSuccess, listQuery.query.isFetching,
    listQuery.query.isPlaceholderData, total, paginationState.pageIndex,
    paginationState.pageSize, resourceView.setPage]);
  const table = useReactTable<TRow>({
    data: rows,
    columns: tableColumns as ColumnDef<TRow>[],
    rowCount: listQuery.result.total,
    pageCount: listQuery.result.total === undefined ? -1 : undefined,
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
    getRowId: modelRowId,
    enableRowSelection: (row) => !row.getIsGrouped(),
    getGroupedRowModel: getGroupedRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    autoResetPageIndex: false,
    autoResetExpanded: false,
  });
  const refetchRows = listQuery.query.refetch;
  const boardLaneState = useBoardLaneState<TRow>({
    laneSource,
    modelMetadata,
    rows,
    enabled: active && resourceView.state.view === "board",
    refetchRows,
  });
  const list = React.useMemo(
    () =>
      listResultFromTable(table, {
        error: listQuery.query.error ?? null,
        fetching: listQuery.query.isFetching
          || boardLaneState.fetching,
        refetch: () => {
          void listQuery.query.refetch();
        },
        rows,
        total: listQuery.result.total,
      }),
    [boardLaneState.fetching, resourceView, rows, listQuery],
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

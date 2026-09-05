import * as React from "react";
import { refineResourceName, type Row } from "@angee/metadata";
import { useList, type HttpError } from "@refinedev/core";
import { useReactTable, getCoreRowModel, getExpandedRowModel, getGroupedRowModel, type ColumnDef, type ExpandedState } from "@tanstack/react-table";
import { crudFiltersFromFilterRecord, refineFieldsFromPaths, refineSortersFromAngeeOrder } from "@angee/refine";
import { useBoardLaneState } from "../resource-view-board-lanes";
import { modelRowId } from "../resource-view-codecs";
import { useResourceViewPresentationSurfaceFromTable } from "./presentation";
import { listResultFromTable, useResourceRowsSnapshot, useResourceViewQueryFacts, useResourceViewTableState } from "./table-state";
import type { ResourceViewSurface, RowRecord, UseResourceViewSurfaceProps } from "./types";
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
  const refineFilters = React.useMemo(
    () => crudFiltersFromFilterRecord(mergedFilter) ?? [],
    [mergedFilter],
  );
  const refineSorters = React.useMemo(
    () => refineSortersFromAngeeOrder(sortOrder) ?? [],
    [sortOrder],
  );
  const listMeta = React.useMemo(
    () => ({ fields: refineFieldsFromPaths(requestedFields) }),
    [requestedFields],
  );
  const tableState = useResourceViewTableState({
    columns,
    resourceView,
    modelMetadata,
    groupStack: rowGroupStack,
    sortOrder,
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
  const resourceName = dataResource ? refineResourceName(dataResource) : "__angee_disabled__";
  const active = enabled && Boolean(dataResource);
  const listQuery = useList<RowRecord, HttpError, RowRecord>({
    resource: resourceName,
    dataProviderName: dataResource?.schemaName,
    pagination: {
      mode: "server",
      currentPage: paginationState.pageIndex + 1,
      pageSize: paginationState.pageSize,
    },
    sorters: refineSorters,
    filters: refineFilters,
    meta: listMeta,
    queryOptions: { enabled: active },
  });
  const rows = listQuery.result.data as TRow[];
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

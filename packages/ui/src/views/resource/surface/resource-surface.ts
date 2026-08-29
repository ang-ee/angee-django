import * as React from "react";
import { refineResourceName, type Row } from "@angee/metadata";
import { useTable as useRefineTable } from "@refinedev/react-table";
import { type HttpError } from "@refinedev/core";
import { getExpandedRowModel, getGroupedRowModel, type ColumnDef, type ExpandedState, type Table as TableModel } from "@tanstack/react-table";
import { crudFiltersFromFilterRecord, refineFieldsFromPaths, refineSortersFromAngeeOrder, stableSerialize } from "@angee/refine";
import { useLatestRef } from "../../../lib/use-latest-ref";
import { useValueStable } from "../../../lib/use-value-stable";
import { useBoardLaneState } from "../resource-view-board-lanes";
import { modelRowId } from "../resource-view-codecs";
import { useResourceViewPresentationSurfaceFromTable } from "./presentation";
import { listResultFromPageState, useResourceRowsSnapshot, useResourceViewQueryFacts, useResourceViewTableState } from "./table-state";
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
  // Value-stabilise the filters/sorters handed to refine's useTable: a
  // consumer's inline `baseFilter`/`order` (e.g. a board's) rebuilds
  // `mergedFilter`/`sortOrder` every render, so these memos yield a fresh
  // array identity each time. refine's internal permanent-filter/sorter sync
  // effect keys on identity and loops ("Maximum update depth") — collapsing
  // value-equal arrays back to one identity stops it.
  const refineFilters = useValueStable(
    React.useMemo(
      () => crudFiltersFromFilterRecord(mergedFilter) ?? [],
      [mergedFilter],
    ),
  );
  const refineFiltersKey = React.useMemo(
    () => stableSerialize(refineFilters),
    [refineFilters],
  );
  const refineSorters = useValueStable(
    React.useMemo(
      () => refineSortersFromAngeeOrder(sortOrder) ?? [],
      [sortOrder],
    ),
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
  const tableResult = useRefineTable<RowRecord, HttpError, RowRecord>({
    columns: tableColumns as ColumnDef<RowRecord>[],
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
    refineCoreProps: {
      resource: resourceName,
      dataProviderName: dataResource?.schemaName,
      pagination: {
        mode: "server",
        currentPage: resourceView.state.page,
        pageSize: resourceView.state.pageSize,
      },
      sorters: {
        mode: "server",
        initial: refineSorters,
      },
      filters: {
        mode: "server",
        permanent: refineFilters,
        defaultBehavior: "replace",
      },
      meta: listMeta,
      queryOptions: { enabled: active },
    },
  });
  // Reset user-applied filters when the permanent/base filter changes. Key
  // ONLY on the value (refineFiltersKey) — refine returns a fresh setFilters
  // identity every render, so depending on it would re-run this effect every
  // commit and loop. Call the latest setFilters through a ref instead.
  const setFiltersRef = useLatestRef(tableResult.refineCore.setFilters);
  React.useEffect(() => {
    setFiltersRef.current([], "replace");
  }, [refineFiltersKey, setFiltersRef]);
  const rows = React.useMemo(
    () => tableResult.refineCore.result.data as readonly TRow[],
    [tableResult.refineCore.result.data],
  );
  const refetchRows = React.useCallback(() => {
    void tableResult.refineCore.tableQuery.refetch();
  }, [tableResult.refineCore.tableQuery.refetch]);
  const boardLaneState = useBoardLaneState<TRow>({
    laneSource,
    modelMetadata,
    rows,
    enabled: active && resourceView.state.view === "board",
    refetchRows,
  });
  const list = React.useMemo(
    () =>
      listResultFromPageState({
        resourceView,
        error: tableResult.refineCore.tableQuery.error,
        fetching: tableResult.refineCore.tableQuery.isFetching
          || boardLaneState.fetching,
        refetch: () => {
          void tableResult.refineCore.tableQuery.refetch();
        },
        rows,
        total: tableResult.refineCore.result.total,
      }),
    [boardLaneState.fetching, resourceView, rows, tableResult.refineCore],
  );
  const listState = useResourceRowsSnapshot<TRow>(list, {
    navigation: { filter: mergedFilter, order: sortOrder },
    onListStateChange,
  });

  const presentation = useResourceViewPresentationSurfaceFromTable({
    rows,
    table: tableResult.reactTable as unknown as TableModel<TRow>,
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

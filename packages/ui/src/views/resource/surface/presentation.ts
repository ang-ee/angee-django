import * as React from "react";
import { type ModelMetadata, type Row } from "@angee/metadata";
import { getCoreRowModel, getExpandedRowModel, getFilteredRowModel, getGroupedRowModel, getPaginationRowModel, getSortedRowModel, useReactTable, type ColumnDef, type ExpandedState, type FilterFn, type Table as TableModel, type VisibilityState } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useUiT } from "../../../i18n";
import type { ResourceViewContextValue } from "../resource-view-context";
import { type ResourceViewFilter, type ResourceViewGroup } from "../resource-view-model";
import { GROUP_ROW_HEIGHT, RECORD_ROW_HEIGHT, isGroupingOnlyColumn, tableColumnLabel, type VisibleFieldOption } from "../resource-view-list-body";
import type { ColumnDescriptor } from "../../page";
import { rowGroupsFromLaneSource, type BoardLaneState } from "../resource-view-board-lanes";
import { idsFromRowSelectionState, leafTableRows, rowGroupsFromTableRows } from "../resource-view-codecs";
import { resourceViewFilterFn, type LocalFilterState } from "../resource-view-client-filter";
import { useResourceViewTableState } from "./table-state";
import { EMPTY_ARRAY, EMPTY_BOARD_PLACEMENTS } from "./types";
import type { FlatResourceViewPresentationSurface, ResourceViewPresentationSurface } from "./types";
export function useResourceViewPresentationSurface<TRow extends Row>({
  rows,
  columns,
  resourceView,
  modelMetadata,
  groupStack,
  getRowId,
  boardLaneState,
  filter,
  textSearchField,
  textSearchFields,
}: {
  rows: readonly TRow[];
  columns: readonly ColumnDescriptor<TRow>[];
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly ResourceViewGroup[];
  getRowId: (row: TRow, index: number) => string;
  boardLaneState?: BoardLaneState<TRow>;
  filter?: ResourceViewFilter;
  textSearchField?: string;
  textSearchFields?: readonly string[];
}): FlatResourceViewPresentationSurface<TRow> {
  const rowGroupStack = groupStack ?? resourceView.state.groupStack;
  const [expanded, setExpanded] = React.useState<ExpandedState>({});
  const tableState = useResourceViewTableState({
    columns,
    resourceView,
    modelMetadata,
    groupStack: rowGroupStack,
  });
  const {
    tableColumns,
    columnVisibility,
    effectiveColumnVisibility,
    setColumnVisibility,
    pagination,
    sorting: sortingState,
    grouping,
    rowSelection,
    handlePaginationChange,
    handleSortingChange,
    handleRowSelectionChange,
  } = tableState;
  const globalFilter = React.useMemo<LocalFilterState>(
    () => ({
      filter,
      textSearchField,
      textSearchFields,
    }),
    [filter, textSearchField, textSearchFields],
  );
  const table = useReactTable<TRow>({
    data: rows as TRow[],
    columns: tableColumns as ColumnDef<TRow>[],
    state: {
      columnVisibility: effectiveColumnVisibility,
      expanded,
      globalFilter,
      grouping,
      pagination,
      rowSelection,
      sorting: sortingState,
    },
    onColumnVisibilityChange: setColumnVisibility,
    onExpandedChange: setExpanded,
    onPaginationChange: handlePaginationChange,
    onRowSelectionChange: handleRowSelectionChange,
    onSortingChange: handleSortingChange,
    getCoreRowModel: getCoreRowModel(),
    enableRowSelection: (row) => !row.getIsGrouped(),
    getFilteredRowModel: getFilteredRowModel(),
    getGroupedRowModel: getGroupedRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    globalFilterFn: resourceViewFilterFn as FilterFn<TRow>,
    getRowId,
    // Pagination/sort/filter/grouping are owned by the resource-view (URL) state, not the
    // table. Without this, TanStack Table auto-resets its own page index whenever the
    // `data` reference changes; that reset fires `onStateChange` → re-render → new
    // `data` identity → reset again, an infinite loop that hard-locks WebKit when a
    // re-render storm (grouped rows + opening the filter popover) keeps it fed.
    autoResetPageIndex: false,
    autoResetExpanded: false,
  });
  const pageCount = table.getPageCount();
  React.useEffect(() => {
    if (resourceView.state.page > pageCount) {
      resourceView.setPage(Math.max(1, pageCount));
    }
  }, [pageCount, resourceView.setPage, resourceView.state.page]);
  return useResourceViewPresentationSurfaceFromTable({
    rows,
    table,
    columnVisibility,
    resourceView,
    groupStack,
    boardLaneState,
  });
}

export function useResourceViewPresentationSurfaceFromTable<TRow extends Row>({
  rows,
  table,
  columnVisibility,
  resourceView,
  groupStack,
  boardLaneState,
}: {
  rows: readonly TRow[];
  table: TableModel<TRow>;
  columnVisibility: VisibilityState;
  resourceView: ResourceViewContextValue;
  groupStack?: readonly ResourceViewGroup[];
  boardLaneState?: BoardLaneState<TRow>;
}): FlatResourceViewPresentationSurface<TRow> {
  const t = useUiT();
  const tableColumns = table.options.columns as readonly ColumnDef<TRow>[];
  const {
    visibleColumnCount,
    visibleFields,
    toggleVisibleField,
  } = useResourceViewTableChrome(table, columnVisibility);

  const rowModels = table.getRowModel().rows;
  const tableRowSelection = table.getState().rowSelection;
  const selectedIds = React.useMemo(
    () => idsFromRowSelectionState(tableRowSelection),
    [tableRowSelection],
  );
  const pageIds = React.useMemo(
    () => leafTableRows(rowModels).map((row) => row.id),
    [rowModels],
  );
  const setPageSelection = React.useCallback(
    (checked: boolean) => {
      table.toggleAllPageRowsSelected(checked);
    },
    [table],
  );
  const rowGroupStack = groupStack ?? resourceView.state.groupStack;
  const groupedRows = React.useMemo(
    () =>
      boardLaneState?.source
        ? boardLaneState.fetching
          ? EMPTY_ARRAY
          : rowGroupsFromLaneSource(
              table.getRowModel().rows,
              boardLaneState.source,
              boardLaneState.lanes,
              boardLaneState.optimisticPlacementByRowId,
              t("list.emptyValue"),
              t("list.unknownValue"),
            )
        : rowGroupsFromTableRows(
            table.getGroupedRowModel().rows,
            rowGroupStack,
            t("list.emptyValue"),
            t,
          ),
    [table, rowGroupStack, rows, boardLaneState, t],
  );
  const tableScrollRef = React.useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: rowModels.length,
    getScrollElement: () => tableScrollRef.current,
    initialRect: { width: 1024, height: 600 },
    estimateSize: (index) =>
      rowModels[index]?.getIsGrouped() ? GROUP_ROW_HEIGHT : RECORD_ROW_HEIGHT,
    overscan: 10,
  });

  return {
    tableColumns,
    table,
    columnVisibility,
    visibleColumnCount,
    visibleFields,
    toggleVisibleField,
    rowModels,
    selectedIds,
    pageIds,
    allPageSelected: table.getIsAllPageRowsSelected(),
    somePageSelected: table.getIsSomePageRowsSelected(),
    setPageSelection,
    groupedRows,
    boardDragEnabled: boardLaneState?.dragEnabled ?? false,
    ...(boardLaneState?.rankField
      ? { boardRankField: boardLaneState.rankField }
      : {}),
    boardOptimisticPlacementByRowId:
      boardLaneState?.optimisticPlacementByRowId ?? EMPTY_BOARD_PLACEMENTS,
    ...(boardLaneState?.onCardMove
      ? { onBoardCardMove: boardLaneState.onCardMove }
      : {}),
    tableScrollRef,
    rowVirtualizer,
  };
}

export function useResourceViewTableChrome<TRow extends Row>(
  table: TableModel<TRow>,
  columnVisibility: VisibilityState,
): Pick<
  ResourceViewPresentationSurface<TRow>,
  "visibleColumnCount" | "visibleFields" | "toggleVisibleField"
> {
  const visibleColumnCount = table
    .getVisibleLeafColumns()
    .filter((column) => !isGroupingOnlyColumn(column.columnDef)).length;
  const visibleFields = React.useMemo<readonly VisibleFieldOption[]>(
    () => {
      const chooserColumns = table
        .getAllLeafColumns()
        .filter((column) => !isGroupingOnlyColumn(column.columnDef));
      const visibleCount = chooserColumns.filter((column) =>
        column.getIsVisible(),
      ).length;
      return chooserColumns.map((column) => {
        const visible = column.getIsVisible();
        return {
          id: column.id,
          label: tableColumnLabel(column),
          visible,
          disabled: visible && visibleCount <= 1,
        };
      });
    },
    [columnVisibility, table],
  );
  const toggleVisibleField = React.useCallback(
    (id: string, visible: boolean) => {
      const column = table.getColumn(id);
      if (!column) return;
      if (!visible && column.getIsVisible() && visibleColumnCount <= 1) return;
      column.toggleVisibility(visible);
    },
    [table, visibleColumnCount],
  );
  return {
    visibleColumnCount,
    visibleFields,
    toggleVisibleField,
  };
}

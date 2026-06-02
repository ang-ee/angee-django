import * as React from "react";
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type Row as TableRowModel,
  type Table as TableModel,
  type VisibilityState,
} from "@tanstack/react-table";
import {
  useVirtualizer,
  type Virtualizer,
} from "@tanstack/react-virtual";
import {
  useResourceList,
  type ResourceTypeName,
  type Row,
  type UseResourceListOptions,
  type UseResourceListResult,
} from "@angee/sdk";

import type { DataToolbarVisibleField } from "../toolbars/DataToolbar";
import type { DataViewContextValue } from "./data-view-context";
import {
  dataViewSortToResourceOrder,
  type DataViewFilter,
  type DataViewGroup,
  type DataViewResourceOrder,
} from "./data-view-model";
import {
  GROUP_ROW_HEIGHT,
  RECORD_ROW_HEIGHT,
  buildColumns,
  groupKey,
  readPath,
  tableColumnLabel,
  type ListRenderItem,
  type RowGroup,
} from "./list-internals";
import type { ColumnDescriptor } from "./page";

type ListFilter = UseResourceListOptions<ResourceTypeName>["filter"];

export interface ListViewState<TRow extends Row = Row> {
  rows: readonly TRow[];
  total: number | undefined;
  page: number;
  pageSize: number;
  pageCount: number | undefined;
  hasNext: boolean;
  hasPrev: boolean;
  fetching: boolean;
}

export interface UseDataViewSurfaceProps<TRow extends Row = Row> {
  model: string;
  columns: readonly ColumnDescriptor<TRow>[];
  fields?: readonly string[];
  filter?: ListFilter;
  order?: UseResourceListOptions<ResourceTypeName>["order"];
  pageSize?: number;
  dataView: DataViewContextValue;
  groupStack?: readonly DataViewGroup[];
  enabled?: boolean;
  onListStateChange?: (state: ListViewState<TRow>) => void;
}

export interface DataViewSurface<TRow extends Row = Row> {
  list: UseResourceListResult;
  listState: ListViewState<TRow>;
  rows: readonly TRow[];
  requestedFields: readonly string[];
  mergedFilter: ListFilter;
  sortOrder: DataViewResourceOrder | undefined;
  tableColumns: readonly ColumnDef<TRow>[];
  table: TableModel<TRow>;
  columnVisibility: VisibilityState;
  visibleColumnCount: number;
  visibleFields: readonly DataToolbarVisibleField[];
  toggleVisibleField: (id: string, visible: boolean) => void;
  rowModels: readonly TableRowModel<TRow>[];
  selectedIds: ReadonlySet<string>;
  pageIds: readonly string[];
  allPageSelected: boolean;
  somePageSelected: boolean;
  setPageSelection: (checked: boolean) => void;
  groupedRows: readonly RowGroup<TRow>[];
  listItems: readonly ListRenderItem<TRow>[];
  tableScrollRef: React.RefObject<HTMLDivElement | null>;
  rowVirtualizer: Virtualizer<HTMLDivElement, Element>;
}

export function useDataViewSurface<TRow extends Row = Row>({
  model,
  columns,
  fields,
  filter,
  order,
  pageSize,
  dataView,
  groupStack,
  enabled = true,
  onListStateChange,
}: UseDataViewSurfaceProps<TRow>): DataViewSurface<TRow> {
  React.useEffect(() => {
    if (pageSize && dataView.state.pageSize !== pageSize) {
      dataView.setPageSize(pageSize);
    }
  }, [dataView.setPageSize, dataView.state.pageSize, pageSize]);

  const requestedFields = React.useMemo(() => {
    const paths = new Set<string>(["id"]);
    for (const column of columns) paths.add(column.field);
    for (const extra of fields ?? []) paths.add(extra);
    return [...paths];
  }, [columns, fields]);

  const mergedFilter = React.useMemo(
    () => mergeFilters(filter, dataView.state.filter),
    [dataView.state.filter, filter],
  );
  const sortOrder = React.useMemo(
    () => dataViewSortToResourceOrder(dataView.state.sort),
    [dataView.state.sort],
  );
  const list = useResourceList(model, {
    fields: requestedFields,
    filter: mergedFilter,
    order: sortOrder ?? order,
    pageSize: dataView.state.pageSize,
    page: dataView.state.page,
    enabled,
  });
  const tableColumns = React.useMemo(
    () => buildColumns(columns, dataView),
    [columns, dataView],
  );
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>({});
  const rows = list.rows as readonly TRow[];
  const listState = React.useMemo<ListViewState<TRow>>(
    () => ({
      rows,
      total: list.total,
      page: list.page,
      pageSize: list.pageSize,
      pageCount: list.pageCount,
      hasNext: list.hasNext,
      hasPrev: list.hasPrev,
      fetching: list.fetching,
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
    ],
  );
  React.useEffect(() => {
    onListStateChange?.(listState);
  }, [listState, onListStateChange]);

  const table = useReactTable<TRow>({
    data: rows as TRow[],
    columns: tableColumns as ColumnDef<TRow>[],
    state: { columnVisibility },
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row, index) =>
      typeof row.id === "string" ? row.id : String(index),
    // Pagination/sort/filter/grouping are owned by the data-view (URL) state, not the
    // table. Without this, TanStack Table auto-resets its own page index whenever the
    // `data` reference changes; that reset fires `onStateChange` → re-render → new
    // `data` identity → reset again, an infinite loop that hard-locks WebKit when a
    // re-render storm (grouped rows + opening the filter popover) keeps it fed.
    autoResetPageIndex: false,
    autoResetExpanded: false,
  });
  const visibleColumnCount = table.getVisibleLeafColumns().length;
  const visibleFields = React.useMemo<readonly DataToolbarVisibleField[]>(
    () => {
      const visibleCount = table.getVisibleLeafColumns().length;
      return table.getAllLeafColumns().map((column) => {
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

  const rowModels = table.getRowModel().rows;
  const selectedIds = dataView.state.selectedIds;
  // Memoize so the surface returns stable references — safe for a memoized
  // FlatListBody and so the freeze guard isn't the only thing absorbing churn.
  const pageIds = React.useMemo(
    () =>
      rows.flatMap((row, index) =>
        typeof row.id === "string" ? [row.id] : [String(index)],
      ),
    [rows],
  );
  const allPageSelected = React.useMemo(
    () => pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id)),
    [pageIds, selectedIds],
  );
  const somePageSelected = React.useMemo(
    () => pageIds.some((id) => selectedIds.has(id)),
    [pageIds, selectedIds],
  );
  const setPageSelection = React.useCallback(
    (checked: boolean) => {
      const next = new Set(dataView.state.selectedIds);
      for (const id of pageIds) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      dataView.setSelectedIds(next);
    },
    [dataView, pageIds],
  );
  const rowGroupStack = groupStack ?? dataView.state.groupStack;
  const groupedRows = React.useMemo(
    () => groupRows(rowModels, rowGroupStack),
    [rowGroupStack, rowModels],
  );
  const listItems = React.useMemo(
    () => flattenListItems(groupedRows),
    [groupedRows],
  );
  const tableScrollRef = React.useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: listItems.length,
    getScrollElement: () => tableScrollRef.current,
    initialRect: { width: 1024, height: 600 },
    estimateSize: (index) =>
      listItems[index]?.kind === "group" ? GROUP_ROW_HEIGHT : RECORD_ROW_HEIGHT,
    overscan: 10,
  });

  return {
    list,
    listState,
    rows,
    requestedFields,
    mergedFilter,
    sortOrder,
    tableColumns,
    table,
    columnVisibility,
    visibleColumnCount,
    visibleFields,
    toggleVisibleField,
    rowModels,
    selectedIds,
    pageIds,
    allPageSelected,
    somePageSelected,
    setPageSelection,
    groupedRows,
    listItems,
    tableScrollRef,
    rowVirtualizer,
  };
}

function groupRows<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  groupStack: readonly DataViewGroup[],
  depth = 0,
  parentPath: readonly string[] = [],
): readonly RowGroup<TRow>[] {
  const [group, ...rest] = groupStack;
  if (!group) {
    return [{
      key: groupPathKey(parentPath) || "root",
      label: null,
      path: parentPath,
      depth,
      rows,
      children: [],
    }];
  }
  const groups = new Map<string, TableRowModel<TRow>[]>();
  for (const row of rows) {
    const key = groupKey(readPath(row.original, group.field), group);
    const next = groups.get(key) ?? [];
    next.push(row);
    groups.set(key, next);
  }
  return [...groups.entries()].map(([label, groupRows]) => {
    const path = [...parentPath, label];
    return {
      key: groupPathKey(path),
      label,
      path,
      depth,
      rows: groupRows,
      children: groupRows.length > 0
        ? groupRowsByRest(groupRows, rest, depth + 1, path)
        : [],
    };
  });
}

function groupRowsByRest<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  groupStack: readonly DataViewGroup[],
  depth: number,
  parentPath: readonly string[],
): readonly RowGroup<TRow>[] {
  return groupRows(rows, groupStack, depth, parentPath).filter(
    (group) => group.label !== null || group.children.length > 0,
  );
}

function flattenListItems<TRow extends Row>(
  groups: readonly RowGroup<TRow>[],
): ListRenderItem<TRow>[] {
  const output: ListRenderItem<TRow>[] = [];
  for (const group of groups) {
    if (group.label !== null) output.push({ kind: "group", group });
    if (group.children.length > 0) {
      output.push(...flattenListItems(group.children));
    } else {
      for (const row of group.rows) output.push({ kind: "row", row });
    }
  }
  return output;
}

function groupPathKey(path: readonly string[]): string {
  return JSON.stringify(path);
}

function mergeFilters(
  base: ListFilter,
  view: DataViewFilter,
): ListFilter {
  if (!base) return Object.keys(view).length > 0 ? view : undefined;
  return { ...base, ...view };
}

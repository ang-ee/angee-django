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
  createLocalRowsDataSource,
  useResourceList,
  rowPublicId,
  type ModelMetadata,
  type ResourceTypeName,
  type Row,
  type UseResourceListOptions,
  type UseResourceListResult,
} from "@angee/sdk";

import type { DataViewContextValue } from "./data-view-context";
import { useExpandedKeys } from "./grouped-list-utils";
import {
  Filter,
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
  type VisibleFieldOption,
} from "./ListInternals";
import type { ColumnDescriptor } from "./page";

type ListFilter = UseResourceListOptions<ResourceTypeName>["filter"];

export type StringIdRow = Row & { id: string };
export { nextRowTextFilter, rowTextFilterValue } from "@angee/sdk";

export interface ListViewState<TRow extends Row = Row> {
  rows: readonly TRow[];
  total: number | undefined;
  page: number;
  pageSize: number;
  pageCount: number | undefined;
  hasNext: boolean;
  hasPrev: boolean;
  fetching: boolean;
  navigationScope?: ListViewNavigationScope;
}

export interface ListViewNavigationScope {
  filter: ListFilter | undefined;
  order: UseResourceListOptions<ResourceTypeName>["order"];
  page: number;
  pageSize: number;
}

export interface UseDataViewSurfaceProps<TRow extends Row = Row> {
  model: string;
  columns: readonly ColumnDescriptor<TRow>[];
  fields?: readonly string[];
  filter?: ListFilter;
  order?: UseResourceListOptions<ResourceTypeName>["order"];
  pageSize?: number;
  dataView: DataViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly DataViewGroup[];
  enabled?: boolean;
  onListStateChange?: (state: ListViewState<TRow>) => void;
}

export interface UseRowsDataViewSurfaceProps<
  TRow extends StringIdRow = StringIdRow,
> {
  rows: readonly TRow[];
  columns: readonly ColumnDescriptor<TRow>[];
  pageSize?: number;
  dataView: DataViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly DataViewGroup[];
  fetching?: boolean;
  error?: Error | null;
  onListStateChange?: (state: ListViewState<TRow>) => void;
}

export interface RowsListState<TRow extends StringIdRow = StringIdRow>
  extends ListViewState<TRow> {
  error: Error | null;
}

interface DataViewPresentationSurface<TRow extends Row = Row> {
  tableColumns: readonly ColumnDef<TRow>[];
  table: TableModel<TRow>;
  columnVisibility: VisibilityState;
  visibleColumnCount: number;
  visibleFields: readonly VisibleFieldOption[];
  toggleVisibleField: (id: string, visible: boolean) => void;
  rowModels: readonly TableRowModel<TRow>[];
  selectedIds: ReadonlySet<string>;
  pageIds: readonly string[];
  allPageSelected: boolean;
  somePageSelected: boolean;
  setPageSelection: (checked: boolean) => void;
  groupedRows: readonly RowGroup<TRow>[];
  listItems: readonly ListRenderItem<TRow>[];
  /** Keys of the groups the viewer has expanded; empty means collapsed-by-default. */
  expandedKeys: ReadonlySet<string>;
  toggleGroup: (key: string) => void;
  tableScrollRef: React.RefObject<HTMLDivElement | null>;
  rowVirtualizer: Virtualizer<HTMLDivElement, Element>;
}

export interface DataViewSurface<TRow extends Row = Row>
  extends DataViewPresentationSurface<TRow> {
  list: UseResourceListResult;
  listState: ListViewState<TRow>;
  rows: readonly TRow[];
  requestedFields: readonly string[];
  mergedFilter: ListFilter;
  sortOrder: DataViewResourceOrder | undefined;
}

export interface RowsDataViewSurface<TRow extends StringIdRow = StringIdRow>
  extends DataViewPresentationSurface<TRow> {
  list: RowsListState<TRow>;
  listState: RowsListState<TRow>;
  rows: readonly TRow[];
  sourceRows: readonly TRow[];
}

export function useSyncPageSize(
  dataView: DataViewContextValue,
  pageSize: number | undefined,
): void {
  const handledPageSizeRef = React.useRef<number | undefined>(undefined);
  React.useEffect(() => {
    if (handledPageSizeRef.current === pageSize) return;
    handledPageSizeRef.current = pageSize;
    if (pageSize !== undefined && dataView.state.pageSize !== pageSize) {
      dataView.setPageSize(pageSize);
    }
  }, [dataView.setPageSize, dataView.state.pageSize, pageSize]);
}

export function useResourceListState<TRow extends Row = Row>(
  list: UseResourceListResult,
  navigationScope?: ListViewNavigationScope,
): ListViewState<TRow> {
  const rows = list.rows as readonly TRow[];
  return React.useMemo<ListViewState<TRow>>(
    () => ({
      rows,
      total: list.total,
      page: list.page,
      pageSize: list.pageSize,
      pageCount: list.pageCount,
      hasNext: list.hasNext,
      hasPrev: list.hasPrev,
      fetching: list.fetching,
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
      navigationScope,
    ],
  );
}

export function useDataViewSurface<TRow extends Row = Row>({
  model,
  columns,
  fields,
  filter,
  order,
  pageSize,
  dataView,
  modelMetadata = null,
  groupStack,
  enabled = true,
  onListStateChange,
}: UseDataViewSurfaceProps<TRow>): DataViewSurface<TRow> {
  useSyncPageSize(dataView, pageSize);

  const requestedFields = React.useMemo(() => {
    const paths = new Set<string>(["id"]);
    for (const column of columns) paths.add(column.field);
    for (const extra of fields ?? []) paths.add(extra);
    return [...paths];
  }, [columns, fields]);

  const mergedFilter = React.useMemo(
    () => Filter.combineOptional(filter, dataView.state.filter),
    [dataView.state.filter, filter],
  );
  const sortOrder = React.useMemo(
    () => dataView.state.resourceOrder(),
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
  const listState = useResourceListState<TRow>(list);
  const rows = listState.rows;
  React.useEffect(() => {
    onListStateChange?.(listState);
  }, [listState, onListStateChange]);

  const presentation = useDataViewPresentationSurface({
    rows,
    columns,
    dataView,
    modelMetadata,
    groupStack,
    getRowId: modelRowId,
  });

  return {
    list,
    listState,
    rows,
    requestedFields,
    mergedFilter,
    sortOrder,
    ...presentation,
  };
}

export function useRowsDataViewSurface<
  TRow extends StringIdRow = StringIdRow,
>({
  rows,
  columns,
  pageSize,
  dataView,
  modelMetadata = null,
  groupStack,
  fetching = false,
  error = null,
  onListStateChange,
}: UseRowsDataViewSurfaceProps<TRow>): RowsDataViewSurface<TRow> {
  useSyncPageSize(dataView, pageSize);

  const source = React.useMemo(
    () => createLocalRowsDataSource(rows),
    [rows],
  );
  const textFields = React.useMemo(
    () => columns.map((column) => column.field),
    [columns],
  );
  const localPage = React.useMemo(
    () =>
      source.query({
        filter: dataView.state.filter,
        sort: dataView.state.sort,
        page: dataView.state.page,
        pageSize: dataView.state.pageSize,
        textFields,
      }),
    [
      dataView.state.filter,
      dataView.state.page,
      dataView.state.pageSize,
      dataView.state.sort,
      source,
      textFields,
    ],
  );

  React.useEffect(() => {
    if (dataView.state.page > localPage.pageCount) {
      dataView.setPage(localPage.pageCount);
    }
  }, [dataView.setPage, dataView.state.page, localPage.pageCount]);

  const pageRows = localPage.rows;
  const listState = React.useMemo<RowsListState<TRow>>(
    () => ({
      rows: pageRows,
      total: localPage.total,
      page: localPage.page,
      pageSize: localPage.pageSize,
      pageCount: localPage.pageCount,
      hasNext: localPage.hasNext,
      hasPrev: localPage.hasPrev,
      fetching,
      error,
    }),
    [
      error,
      fetching,
      localPage.hasNext,
      localPage.hasPrev,
      localPage.page,
      localPage.pageCount,
      localPage.pageSize,
      localPage.total,
      pageRows,
    ],
  );
  React.useEffect(() => {
    onListStateChange?.(listState);
  }, [listState, onListStateChange]);

  const presentation = useDataViewPresentationSurface({
    rows: pageRows,
    columns,
    dataView,
    modelMetadata,
    groupStack,
    getRowId: stringRowId,
  });

  return {
    list: listState,
    listState,
    rows: pageRows,
    sourceRows: rows,
    ...presentation,
  };
}

function useDataViewPresentationSurface<TRow extends Row>({
  rows,
  columns,
  dataView,
  modelMetadata,
  groupStack,
  getRowId,
}: {
  rows: readonly TRow[];
  columns: readonly ColumnDescriptor<TRow>[];
  dataView: DataViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly DataViewGroup[];
  getRowId: (row: TRow, index: number) => string;
}): DataViewPresentationSurface<TRow> {
  const tableColumns = React.useMemo(
    () => buildColumns(columns, dataView),
    [columns, dataView],
  );
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>({});
  const table = useReactTable<TRow>({
    data: rows as TRow[],
    columns: tableColumns as ColumnDef<TRow>[],
    state: { columnVisibility },
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getRowId,
    // Pagination/sort/filter/grouping are owned by the data-view (URL) state, not the
    // table. Without this, TanStack Table auto-resets its own page index whenever the
    // `data` reference changes; that reset fires `onStateChange` → re-render → new
    // `data` identity → reset again, an infinite loop that hard-locks WebKit when a
    // re-render storm (grouped rows + opening the filter popover) keeps it fed.
    autoResetPageIndex: false,
    autoResetExpanded: false,
  });
  const visibleColumnCount = table.getVisibleLeafColumns().length;
  const visibleFields = React.useMemo<readonly VisibleFieldOption[]>(
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
    () => rows.map((row, index) => getRowId(row, index)),
    [getRowId, rows],
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
    () => groupRows(rowModels, rowGroupStack, modelMetadata),
    [modelMetadata, rowGroupStack, rowModels],
  );
  // Collapse is the framework default for grouped rows: groups start collapsed
  // and the viewer expands them. The state machine is shared with GroupedList.
  const { expandedKeys, toggle: toggleGroup } = useExpandedKeys();
  const listItems = React.useMemo(
    () => flattenListItems(groupedRows, expandedKeys),
    [expandedKeys, groupedRows],
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
    expandedKeys,
    toggleGroup,
    tableScrollRef,
    rowVirtualizer,
  };
}

function modelRowId<TRow extends Row>(row: TRow, index: number): string {
  return rowPublicId(row) ?? String(index);
}

function stringRowId<TRow extends StringIdRow>(row: TRow): string {
  return row.id;
}

function groupRows<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  groupStack: readonly DataViewGroup[],
  modelMetadata: ModelMetadata | null = null,
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
    const key = groupKey(readPath(row.original, group.field), group, modelMetadata);
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
        ? groupRowsByRest(groupRows, rest, modelMetadata, depth + 1, path)
        : [],
    };
  });
}

function groupRowsByRest<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  groupStack: readonly DataViewGroup[],
  modelMetadata: ModelMetadata | null,
  depth: number,
  parentPath: readonly string[],
): readonly RowGroup<TRow>[] {
  return groupRows(rows, groupStack, modelMetadata, depth, parentPath).filter(
    (group) => group.label !== null || group.children.length > 0,
  );
}

// Flatten the group tree to the virtualizer's render list, emitting a collapsed
// group's header but none of its body. Re-flattening expanded-only (rather than
// post-filtering rows) keeps the virtualizer count and estimated heights exact.
function flattenListItems<TRow extends Row>(
  groups: readonly RowGroup<TRow>[],
  expandedKeys: ReadonlySet<string>,
): ListRenderItem<TRow>[] {
  const output: ListRenderItem<TRow>[] = [];
  for (const group of groups) {
    const hasHeader = group.label !== null;
    if (hasHeader) output.push({ kind: "group", group });
    // The label-less root carries no header and is always open; a real group is
    // open only when the viewer has expanded its key.
    if (hasHeader && !expandedKeys.has(group.key)) continue;
    if (group.children.length > 0) {
      output.push(...flattenListItems(group.children, expandedKeys));
    } else {
      for (const row of group.rows) output.push({ kind: "row", row });
    }
  }
  return output;
}

function groupPathKey(path: readonly string[]): string {
  return JSON.stringify(path);
}

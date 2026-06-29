import * as React from "react";
import {
  rowPublicId,
  type Row,
} from "@angee/resources";
import {
  useTable as useRefineTable } from "@refinedev/react-table";
import {
  useList,
  type BaseRecord,
  type HttpError,
  type MetaQuery,
  } from "@refinedev/core";
import {
  functionalUpdate,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type OnChangeFn,
  type PaginationState,
  type Row as TableRowModel,
  type SortingState,
  type Table as TableModel,
  type VisibilityState,
  } from "@tanstack/react-table";
import {
  useVirtualizer,
  type Virtualizer,
  } from "@tanstack/react-virtual";
import {
  crudFiltersFromFilterRecord,
  refineFieldsFromPaths,
  refineSortersFromAngeeOrder,
  } from "@angee/refine";
import {
  refineResourceName,
} from "@angee/resources";
import type {
  DataResourceDefaultSortMetadata,
} from "@angee/resources";
import type {
  ModelMetadata,
} from "@angee/resources";

import type { ResourceViewContextValue } from "./resource-view-context";
import { useExpandedKeys } from "./grouped-list-utils";
import {
  Filter,
  type ResourceViewGroup,
} from "./resource-view-model";
import {
  createLocalRowsDataSource,
  nextRowTextFilter,
  rowTextFilterValue,
  useLocalRowsPage,
} from "./local-rows";
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

type ListFilter = Record<string, unknown>;
type ListOrder = Record<string, unknown>;
type RowRecord = BaseRecord & Row;

export type StringIdRow = Row & { id: string };
export { nextRowTextFilter, rowTextFilterValue };

export interface ResourceListSnapshot<TRow extends Row = Row> {
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
  order: ListOrder | undefined;
  page: number;
  pageSize: number;
}

export interface UseResourceViewSurfaceProps<TRow extends Row = Row> {
  resource: string;
  columns: readonly ColumnDescriptor<TRow>[];
  fields?: readonly string[];
  filter?: ListFilter;
  order?: ListOrder;
  pageSize?: number;
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly ResourceViewGroup[];
  enabled?: boolean;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

export interface UseRowsResourceViewSurfaceProps<
  TRow extends StringIdRow = StringIdRow,
> {
  rows: readonly TRow[];
  columns: readonly ColumnDescriptor<TRow>[];
  pageSize?: number;
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly ResourceViewGroup[];
  fetching?: boolean;
  error?: Error | null;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

export interface RowsResourceListSnapshot<TRow extends StringIdRow = StringIdRow>
  extends ResourceListSnapshot<TRow> {
  error: Error | null;
}

export interface ResourceListResult {
  rows: readonly Row[];
  total: number | undefined;
  pageCount: number | undefined;
  page: number;
  pageSize: number;
  pageInfo: undefined;
  hasNext: boolean;
  hasPrev: boolean;
  setPage: (page: number) => void;
  firstPage: () => void;
  nextPage: () => void;
  prevPage: () => void;
  lastPage: () => void;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

interface ResourceViewPresentationSurface<TRow extends Row = Row> {
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

export interface ResourceViewSurface<TRow extends Row = Row>
  extends ResourceViewPresentationSurface<TRow> {
  list: ResourceListResult;
  listState: ResourceListSnapshot<TRow>;
  rows: readonly TRow[];
  requestedFields: readonly string[];
  mergedFilter: ListFilter | undefined;
  sortOrder: ListOrder | undefined;
}

const EMPTY_ARRAY = [] as const;
const EMPTY_SELECTED_IDS: ReadonlySet<string> = new Set();

export interface RowsResourceViewSurface<TRow extends StringIdRow = StringIdRow>
  extends ResourceViewPresentationSurface<TRow> {
  list: RowsResourceListSnapshot<TRow>;
  listState: RowsResourceListSnapshot<TRow>;
  rows: readonly TRow[];
  sourceRows: readonly TRow[];
}

export function useSyncPageSize(
  resourceView: ResourceViewContextValue,
  pageSize: number | undefined,
): void {
  const handledPageSizeRef = React.useRef<number | undefined>(undefined);
  React.useEffect(() => {
    if (handledPageSizeRef.current === pageSize) return;
    handledPageSizeRef.current = pageSize;
    if (pageSize !== undefined && resourceView.state.pageSize !== pageSize) {
      resourceView.setPageSize(pageSize);
    }
  }, [resourceView.setPageSize, resourceView.state.pageSize, pageSize]);
}

export function useResourceRowsSnapshot<TRow extends Row = Row>(
  list: ResourceListResult,
  navigationScope?: ListViewNavigationScope,
): ResourceListSnapshot<TRow> {
  const rows = list.rows as readonly TRow[];
  return React.useMemo<ResourceListSnapshot<TRow>>(
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

function listResultFromRefineTable<TRow extends Row>({
  resourceView,
  error,
  fetching,
  refetch,
  rows,
  total,
}: {
  resourceView: ResourceViewContextValue;
  error: unknown;
  fetching: boolean;
  refetch: () => void;
  rows: readonly TRow[];
  total: number | undefined;
}): ResourceListResult {
  const page = resourceView.state.page;
  const pageSize = resourceView.state.pageSize;
  const pageCount = total === undefined
    ? undefined
    : Math.max(1, Math.ceil(total / pageSize));
  return {
    rows,
    total,
    pageCount,
    page,
    pageSize,
    pageInfo: undefined,
    hasNext: pageCount !== undefined && page < pageCount,
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

export function useGroupedResourceViewSurface<TRow extends Row = Row>({
  columns,
  fields,
  filter,
  order,
  pageSize,
  resourceView,
  modelMetadata = null,
}: UseResourceViewSurfaceProps<TRow>): ResourceViewSurface<TRow> {
  useSyncPageSize(resourceView, pageSize);

  const requestedFields = React.useMemo(
    () => requestedFieldPaths(columns, fields, modelMetadata),
    [columns, fields, modelMetadata],
  );
  const mergedFilter = React.useMemo(
    () => Filter.combineOptional(filter, resourceView.state.filter),
    [resourceView.state.filter, filter],
  );
  const sortOrder = React.useMemo(
    () =>
      resourceView.state.resourceOrder()
      ?? order
      ?? defaultResourceOrder(modelMetadata),
    [resourceView.state.sort, modelMetadata, order],
  );
  const tableColumns = React.useMemo(
    () => buildColumns(columns, resourceView),
    [columns, resourceView],
  );
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>({});
  const table = useReactTable<TRow>({
    data: EMPTY_ARRAY as readonly TRow[] as TRow[],
    columns: tableColumns as ColumnDef<TRow>[],
    state: { columnVisibility },
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getRowId: modelRowId,
    autoResetPageIndex: false,
    autoResetExpanded: false,
  });
  const {
    visibleColumnCount,
    visibleFields,
    toggleVisibleField,
  } = useResourceViewTableChrome(table, columnVisibility);
  const tableScrollRef = React.useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: 0,
    getScrollElement: () => tableScrollRef.current,
    initialRect: { width: 1024, height: 600 },
    estimateSize: () => RECORD_ROW_HEIGHT,
    overscan: 0,
  });
  const list = React.useMemo<ResourceListResult>(
    () => ({
      rows: EMPTY_ARRAY,
      total: undefined,
      pageCount: undefined,
      page: resourceView.state.page,
      pageSize: resourceView.state.pageSize,
      pageInfo: undefined,
      hasNext: false,
      hasPrev: resourceView.state.page > 1,
      setPage: resourceView.setPage,
      firstPage: () => resourceView.setPage(1),
      nextPage: () => resourceView.setPage(resourceView.state.page + 1),
      prevPage: () => resourceView.setPage(Math.max(1, resourceView.state.page - 1)),
      lastPage: () => undefined,
      fetching: false,
      error: null,
      refetch: () => undefined,
    }),
    [
      resourceView.setPage,
      resourceView.state.page,
      resourceView.state.pageSize,
    ],
  );
  const listState = useResourceRowsSnapshot<TRow>(list);

  return {
    list,
    listState,
    rows: EMPTY_ARRAY as readonly TRow[],
    requestedFields,
    mergedFilter,
    sortOrder,
    tableColumns,
    table,
    columnVisibility,
    visibleColumnCount,
    visibleFields,
    toggleVisibleField,
    rowModels: EMPTY_ARRAY,
    selectedIds: resourceView.state.selectedIds ?? EMPTY_SELECTED_IDS,
    pageIds: EMPTY_ARRAY,
    allPageSelected: false,
    somePageSelected: false,
    setPageSelection: () => undefined,
    groupedRows: EMPTY_ARRAY,
    listItems: EMPTY_ARRAY,
    expandedKeys: EMPTY_SELECTED_IDS,
    toggleGroup: () => undefined,
    tableScrollRef,
    rowVirtualizer,
  };
}

export function useResourceViewSurface<TRow extends Row = Row>({
  columns,
  fields,
  filter,
  order,
  pageSize,
  resourceView,
  modelMetadata = null,
  groupStack,
  enabled = true,
  onListStateChange,
}: UseResourceViewSurfaceProps<TRow>): ResourceViewSurface<TRow> {
  useSyncPageSize(resourceView, pageSize);

  const requestedFields = React.useMemo(
    () => requestedFieldPaths(columns, fields, modelMetadata),
    [columns, fields, modelMetadata],
  );

  const mergedFilter = React.useMemo(
    () => Filter.combineOptional(filter, resourceView.state.filter),
    [resourceView.state.filter, filter],
  );
  const sortOrder = React.useMemo(
    () =>
      resourceView.state.resourceOrder()
      ?? order
      ?? defaultResourceOrder(modelMetadata),
    [resourceView.state.sort, modelMetadata, order],
  );
  const tableColumns = React.useMemo(
    () => buildColumns(columns, resourceView),
    [columns, resourceView],
  );
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>({});
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
  const paginationState = React.useMemo<PaginationState>(
    () => ({
      pageIndex: Math.max(0, resourceView.state.page - 1),
      pageSize: resourceView.state.pageSize,
    }),
    [resourceView.state.page, resourceView.state.pageSize],
  );
  const sortingState = React.useMemo<SortingState>(
    () =>
      refineSorters.map((sorter) => ({
        id: sorter.field,
        desc: sorter.order === "desc",
      })),
    [refineSorters],
  );
  const handlePaginationChange = React.useCallback<OnChangeFn<PaginationState>>(
    (updater) => {
      const next = functionalUpdate(updater, paginationState);
      if (next.pageSize !== resourceView.state.pageSize) {
        resourceView.setPageSize(next.pageSize);
      }
      const nextPage = next.pageIndex + 1;
      if (nextPage !== resourceView.state.page) resourceView.setPage(nextPage);
    },
    [resourceView, paginationState],
  );
  const handleSortingChange = React.useCallback<OnChangeFn<SortingState>>(
    (updater) => {
      const [next] = functionalUpdate(updater, sortingState);
      resourceView.setSort(
        next ? { field: next.id, dir: next.desc ? "desc" : "asc" } : null,
      );
    },
    [resourceView, sortingState],
  );
  const resourceName = dataResource ? refineResourceName(dataResource) : "__angee_disabled__";
  const active = enabled && Boolean(dataResource);
  const tableResult = useRefineTable<RowRecord, HttpError, RowRecord>({
    columns: tableColumns as ColumnDef<RowRecord>[],
    state: {
      columnVisibility,
      pagination: paginationState,
      sorting: sortingState,
    },
    onColumnVisibilityChange: setColumnVisibility,
    onPaginationChange: handlePaginationChange,
    onSortingChange: handleSortingChange,
    getRowId: modelRowId,
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
        initial: refineFilters,
      },
      meta: listMeta,
      queryOptions: { enabled: active },
    },
  });
  const rows = React.useMemo(
    () => tableResult.refineCore.result.data as readonly TRow[],
    [tableResult.refineCore.result.data],
  );
  const list = React.useMemo(
    () =>
      listResultFromRefineTable({
        resourceView,
        error: tableResult.refineCore.tableQuery.error,
        fetching: tableResult.refineCore.tableQuery.isFetching,
        refetch: () => {
          void tableResult.refineCore.tableQuery.refetch();
        },
        rows,
        total: tableResult.refineCore.result.total,
      }),
    [resourceView, rows, tableResult.refineCore],
  );
  const listState = useResourceRowsSnapshot<TRow>(list);
  React.useEffect(() => {
    onListStateChange?.(listState);
  }, [listState, onListStateChange]);

  const presentation = useResourceViewPresentationSurfaceFromTable({
    rows,
    table: tableResult.reactTable as unknown as TableModel<TRow>,
    columnVisibility,
    resourceView,
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

/** Max rows a client resource fetches in one page; warn (never truncate silently) at the cap. */
const CLIENT_ROW_MODEL_FETCH_CAP = 1000;

/**
 * Surface a **client row-model** resource: fetch the whole set once (up to
 * ``CLIENT_ROW_MODEL_FETCH_CAP``) and filter/sort/paginate it in the browser
 * with the same Angee dialect engine the rows surface uses. The sibling of
 * :func:`useResourceViewSurface` (which keeps every list op on the server) — a
 * caller picks one by ``isClientRowModel(resource)`` at a component boundary, so
 * only the active path issues a query and resolves a data provider.
 */
export function useClientResourceViewSurface<TRow extends Row = Row>({
  columns,
  fields,
  filter,
  pageSize,
  resourceView,
  modelMetadata = null,
  groupStack,
  enabled = true,
  onListStateChange,
}: UseResourceViewSurfaceProps<TRow>): ResourceViewSurface<TRow> {
  useSyncPageSize(resourceView, pageSize);

  const requestedFields = React.useMemo(
    () => requestedFieldPaths(columns, fields, modelMetadata),
    [columns, fields, modelMetadata],
  );

  const mergedFilter = React.useMemo(
    () => Filter.combineOptional(filter, resourceView.state.filter),
    [resourceView.state.filter, filter],
  );
  const sortOrder = React.useMemo(
    () => resourceView.state.resourceOrder() ?? undefined,
    [resourceView.state.sort],
  );
  const dataResource = modelMetadata?.resource ?? null;
  const resourceName = dataResource ? refineResourceName(dataResource) : "__angee_disabled__";
  const listMeta = React.useMemo<MetaQuery>(
    () => ({ fields: refineFieldsFromPaths(requestedFields) }),
    [requestedFields],
  );
  const active = enabled && Boolean(dataResource);

  const run = useList<RowRecord, HttpError>({
    resource: resourceName,
    dataProviderName: dataResource?.schemaName,
    pagination: {
      mode: "server",
      currentPage: 1,
      pageSize: CLIENT_ROW_MODEL_FETCH_CAP,
    },
    meta: listMeta,
    queryOptions: { enabled: active },
  });
  const allRows = React.useMemo(
    () => (run.result.data ?? []) as readonly RowRecord[] as readonly TRow[],
    [run.result.data],
  );
  // The fetched page is capped; the only signal the in-browser set is actually
  // incomplete is the resource's own total exceeding the cap (a page that
  // returned exactly the cap is not necessarily truncated).
  const totalRows = run.result.total;
  React.useEffect(() => {
    if (totalRows !== undefined && totalRows > CLIENT_ROW_MODEL_FETCH_CAP) {
      console.warn(
        `Client resource "${dataResource?.modelLabel ?? resourceName}" has ` +
          `${totalRows} rows, above the ${CLIENT_ROW_MODEL_FETCH_CAP}-row client ` +
          "fetch cap; in-browser filter/sort/group is incomplete. " +
          'Mark the resource rowModel="server" or narrow it.',
      );
    }
  }, [totalRows, dataResource?.modelLabel, resourceName]);

  const source = React.useMemo(
    () => createLocalRowsDataSource(allRows),
    [allRows],
  );
  const localPage = useLocalRowsPage({
    source,
    columns,
    resourceView,
    filter: mergedFilter,
  });

  const fetching = run.query.isFetching;
  const error = errorFromUnknown(run.query.error);
  const refetch = React.useCallback(() => {
    void run.query.refetch();
  }, [run.query]);
  const list = React.useMemo<ResourceListResult>(
    () => ({
      rows: localPage.rows,
      total: localPage.total,
      pageCount: localPage.pageCount,
      page: localPage.page,
      pageSize: localPage.pageSize,
      pageInfo: undefined,
      hasNext: localPage.hasNext,
      hasPrev: localPage.hasPrev,
      setPage: resourceView.setPage,
      firstPage: () => resourceView.setPage(1),
      nextPage: () =>
        resourceView.setPage(Math.min(localPage.page + 1, localPage.pageCount)),
      prevPage: () => resourceView.setPage(Math.max(1, localPage.page - 1)),
      lastPage: () => resourceView.setPage(localPage.pageCount),
      fetching,
      error,
      refetch,
    }),
    [localPage, fetching, error, refetch, resourceView.setPage],
  );
  const listState = useResourceRowsSnapshot<TRow>(list);
  React.useEffect(() => {
    onListStateChange?.(listState);
  }, [listState, onListStateChange]);

  const presentation = useResourceViewPresentationSurface<TRow>({
    rows: localPage.rows as readonly TRow[],
    columns,
    resourceView,
    modelMetadata,
    groupStack,
    getRowId: modelRowId,
  });

  return {
    list,
    listState,
    rows: localPage.rows as readonly TRow[],
    requestedFields,
    mergedFilter,
    sortOrder,
    ...presentation,
  };
}

export function useRowsResourceViewSurface<
  TRow extends StringIdRow = StringIdRow,
>({
  rows,
  columns,
  pageSize,
  resourceView,
  modelMetadata = null,
  groupStack,
  fetching = false,
  error = null,
  onListStateChange,
}: UseRowsResourceViewSurfaceProps<TRow>): RowsResourceViewSurface<TRow> {
  useSyncPageSize(resourceView, pageSize);

  const source = React.useMemo(
    () => createLocalRowsDataSource(rows),
    [rows],
  );
  const localPage = useLocalRowsPage({
    source,
    columns,
    resourceView,
    filter: resourceView.state.filter,
  });

  const pageRows = localPage.rows;
  const listState = React.useMemo<RowsResourceListSnapshot<TRow>>(
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

  const presentation = useResourceViewPresentationSurface({
    rows: pageRows,
    columns,
    resourceView,
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

function useResourceViewPresentationSurface<TRow extends Row>({
  rows,
  columns,
  resourceView,
  modelMetadata,
  groupStack,
  getRowId,
}: {
  rows: readonly TRow[];
  columns: readonly ColumnDescriptor<TRow>[];
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly ResourceViewGroup[];
  getRowId: (row: TRow, index: number) => string;
}): ResourceViewPresentationSurface<TRow> {
  const tableColumns = React.useMemo(
    () => buildColumns(columns, resourceView),
    [columns, resourceView],
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
    // Pagination/sort/filter/grouping are owned by the resource-view (URL) state, not the
    // table. Without this, TanStack Table auto-resets its own page index whenever the
    // `data` reference changes; that reset fires `onStateChange` → re-render → new
    // `data` identity → reset again, an infinite loop that hard-locks WebKit when a
    // re-render storm (grouped rows + opening the filter popover) keeps it fed.
    autoResetPageIndex: false,
    autoResetExpanded: false,
  });
  return useResourceViewPresentationSurfaceFromTable({
    rows,
    table,
    columnVisibility,
    resourceView,
    modelMetadata,
    groupStack,
    getRowId,
  });
}

function useResourceViewPresentationSurfaceFromTable<TRow extends Row>({
  rows,
  table,
  columnVisibility,
  resourceView,
  modelMetadata,
  groupStack,
  getRowId,
}: {
  rows: readonly TRow[];
  table: TableModel<TRow>;
  columnVisibility: VisibilityState;
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly ResourceViewGroup[];
  getRowId: (row: TRow, index: number) => string;
}): ResourceViewPresentationSurface<TRow> {
  const tableColumns = table.options.columns as readonly ColumnDef<TRow>[];
  const {
    visibleColumnCount,
    visibleFields,
    toggleVisibleField,
  } = useResourceViewTableChrome(table, columnVisibility);

  const rowModels = table.getRowModel().rows;
  const selectedIds = resourceView.state.selectedIds;
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
      const next = new Set(resourceView.state.selectedIds);
      for (const id of pageIds) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      resourceView.setSelectedIds(next);
    },
    [resourceView, pageIds],
  );
  const rowGroupStack = groupStack ?? resourceView.state.groupStack;
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

function useResourceViewTableChrome<TRow extends Row>(
  table: TableModel<TRow>,
  columnVisibility: VisibilityState,
): Pick<
  ResourceViewPresentationSurface<TRow>,
  "visibleColumnCount" | "visibleFields" | "toggleVisibleField"
> {
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
  return {
    visibleColumnCount,
    visibleFields,
    toggleVisibleField,
  };
}

function requestedFieldPaths<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  extraFields: readonly string[] | undefined,
  modelMetadata: ModelMetadata | null | undefined,
): readonly string[] {
  // A render-only column (e.g. an actions button) carries a `field` that is not
  // a real resource field; including it in the GraphQL selection makes the
  // Hasura provider request an unknown field and the server rejects the query.
  // Keep only fields the resource exposes when the field set is known; without
  // metadata, keep all (the prior behaviour).
  const known = modelMetadata?.resource?.fields;
  const knownNames =
    known && known.length > 0
      ? new Set(known.map((field) => field.name))
      : null;
  const paths = new Set<string>(["id"]);
  for (const column of columns) {
    if (knownNames === null || knownNames.has(column.field)) {
      paths.add(column.field);
    }
  }
  for (const extra of extraFields ?? []) paths.add(extra);
  return [...paths];
}


function modelRowId<TRow extends Row>(row: TRow, index: number): string {
  return rowPublicId(row) ?? String(index);
}

function defaultResourceOrder(
  modelMetadata: ModelMetadata | null | undefined,
): ListOrder | undefined {
  // The current resource-hook order input is single-field; keep the full
  // metadata defaultSort for Hasura/refine, but project the primary term here.
  const [sort] = modelMetadata?.resource?.defaultSort ?? [];
  if (!sort) return undefined;
  return { [sort.field]: defaultSortDirection(sort) };
}

function defaultSortDirection(
  sort: DataResourceDefaultSortMetadata,
): "ASC" | "DESC" {
  return sort.direction.toUpperCase() === "DESC" ? "DESC" : "ASC";
}

function stringRowId<TRow extends StringIdRow>(row: TRow): string {
  return row.id;
}

function errorFromUnknown(error: unknown): Error | null {
  if (!error) return null;
  if (error instanceof Error) return error;
  if (typeof error === "object" && "message" in error) {
    return Object.assign(
      new Error(String((error as { message?: unknown }).message ?? "Unknown error")),
      error,
    );
  }
  return new Error(String(error));
}

function groupRows<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  groupStack: readonly ResourceViewGroup[],
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
  groupStack: readonly ResourceViewGroup[],
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

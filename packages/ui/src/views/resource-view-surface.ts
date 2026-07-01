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
  hasuraWhereFromCrudFilters,
  refineFieldsFromPaths,
  refineSortersFromAngeeOrder,
  type AggregateBucket,
  type GroupByRequestOptions,
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
  useAngeeAggregate,
  useAngeeGroupByBatch,
  useAngeeListBatch,
  type AngeeListBatchEntry,
  type AngeeListBatchScope,
  type GroupByBatchScope,
  type UseAngeeGroupByResult,
} from "../data/hooks";
import {
  Filter,
  stableSerialize,
  type ResourceViewFilter,
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
  bucketFilterForGroup,
  bucketValueLabels,
  buildColumns,
  estimateGroupedItemSize,
  groupFieldLabel,
  groupKey,
  groupLabelDimension,
  groupMeasuresFromColumns,
  hasuraGroupDimension,
  hasuraGroupOrderForDimensions,
  hasuraMeasuresFromGroupMeasures,
  readPath,
  resourceViewGroupToAggregateDimension,
  tableColumnLabel,
  type GroupByDimension,
  type GroupedListItem,
  type GroupedRecordNav,
  type GroupMeasure,
  type ListRenderItem,
  type RowGroup,
  type VisibleFieldOption,
} from "./ListInternals";
import type { ColumnDescriptor } from "./page";

/** Leaf record page size inside a server-grouped bucket. */
const GROUPED_LEAF_PAGE_SIZE = 20;

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
  /** Grand-total measure footer (server-grouped surface); null otherwise. */
  footerAggregate: AggregateBucket | null;
  /** Set a server-grouped sub-group/leaf scope's page; no-op on flat surfaces. */
  setScopePage: (key: string, page: number) => void;
  /** The windowed server-grouped render stream; empty on flat surfaces. */
  groupedItems: readonly GroupedListItem<TRow>[];
}

const EMPTY_ARRAY = [] as const;
const EMPTY_SELECTED_IDS: ReadonlySet<string> = new Set();
const EMPTY_LEAF_RESULTS: ReadonlyMap<string, AngeeListBatchEntry> = new Map();
const NOOP_SET_SCOPE_PAGE = (_key: string, _page: number): void => undefined;

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

/**
 * The server-grouped list surface: the one owner of a folded group view's render
 * model. It emits a single measured `listItems` stream (per-level `_groups`
 * headers, the leaf record rows of expanded buckets, and the per-group pagers)
 * driving the same `useVirtualizer` the flat list uses, batches every `_groups`
 * level into one `useAngeeGroupByBatch` and every expanded leaf into one
 * `useAngeeListBatch`, and exposes per-group pagination via `setScopePage`. The
 * thin {@link GroupedListBody} composes this surface; it no longer fetches.
 */
export function useGroupedResourceViewSurface<TRow extends Row = Row>({
  resource,
  columns,
  fields,
  filter,
  order,
  pageSize,
  resourceView,
  modelMetadata = null,
  groupStack,
}: UseResourceViewSurfaceProps<TRow>): ResourceViewSurface<TRow> {
  useSyncPageSize(resourceView, pageSize);
  const dataResource = requireGroupedDataResource(resource, modelMetadata);

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
  const leafOrder = React.useMemo<ListOrder | undefined>(
    () => sortOrder ?? order,
    [sortOrder, order],
  );
  const rowGroupStack = groupStack ?? resourceView.state.groupStack;
  const rootPage = resourceView.state.page;
  const statePageSize = resourceView.state.pageSize;

  // Columns + per-group/footer measures.
  const tableColumns = React.useMemo(
    () =>
      buildColumns(columns, {
        sort: resourceView.state.sort,
        setSort: resourceView.setSort,
      }),
    [columns, resourceView.state.sort, resourceView.setSort],
  );
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>({});
  const measures = React.useMemo(
    () => groupMeasuresFromColumns(columns),
    [columns],
  );
  const queryMeasures = React.useMemo(
    () => hasuraMeasuresFromGroupMeasures(measures, modelMetadata),
    [measures, modelMetadata],
  );
  const where = React.useMemo(
    () => hasuraWhereFromCrudFilters(crudFiltersFromFilterRecord(mergedFilter)),
    [mergedFilter],
  );
  const grandTotal = useAngeeAggregate(dataResource, {
    where,
    measures: queryMeasures,
    enabled: rowGroupStack.length > 0 && measures.length > 0,
  });

  // Collapse state and per-scope pager pages (one map, keyed by cumulative scope).
  const { expandedKeys, toggle: toggleGroup } = useExpandedKeys();
  const [pageByScope, setPageByScope] =
    React.useState<Record<string, number>>({});
  const setScopePage = React.useCallback((key: string, page: number) => {
    setPageByScope((current) => ({ ...current, [key]: normaliseScopePage(page) }));
  }, []);

  const renderParams = React.useMemo<GroupedRenderParams>(
    () => ({
      groupStack: rowGroupStack,
      baseFilter: mergedFilter,
      expandedKeys,
      pageByScope,
      rootPage,
      pageSize: statePageSize,
      queryMeasures,
      leafOrder,
      modelMetadata,
    }),
    [
      rowGroupStack,
      mergedFilter,
      expandedKeys,
      pageByScope,
      rootPage,
      statePageSize,
      queryMeasures,
      leafOrder,
      modelMetadata,
    ],
  );

  // Per-level `_groups` requests stage over renders: the desired scope frontier is
  // derived from the resolved buckets, so it grows one level deeper each time a
  // parent resolves. `useAngeeGroupByBatch` is a single hook, so a dynamic-length
  // array is rules-of-hooks safe.
  const [groupScopes, setGroupScopes] =
    React.useState<readonly GroupByBatchScope[]>(EMPTY_ARRAY);
  const groupByResults = useAngeeGroupByBatch(dataResource, groupScopes, {
    enabled: rowGroupStack.length > 0,
  });
  const scopeModel = React.useMemo(
    () =>
      buildGroupedRenderModel<TRow>(
        groupByResults,
        EMPTY_LEAF_RESULTS,
        new Map<string, readonly TableRowModel<TRow>[]>(),
        renderParams,
      ),
    [groupByResults, renderParams],
  );
  const desiredGroupScopes = scopeModel.groupScopes;
  const leafScopes = scopeModel.leafScopes;
  React.useEffect(() => {
    setGroupScopes((current) =>
      groupScopesEqual(current, desiredGroupScopes) ? current : desiredGroupScopes,
    );
  }, [desiredGroupScopes]);

  // Every expanded leaf bucket's record page, batched into one request round.
  const leafResults = useAngeeListBatch(dataResource, leafScopes, {
    fields: requestedFields,
    enabled: leafScopes.length > 0,
  });

  // One table over the in-display-order concatenation of loaded leaf rows: row
  // ids stay the bare public id so selection identity matches the flat surface.
  const leafRows = React.useMemo(
    () =>
      leafScopes.flatMap((scope) => [
        ...((leafResults.get(scope.key)?.rows ?? EMPTY_ARRAY) as readonly TRow[]),
      ]),
    [leafScopes, leafResults],
  );
  const table = useReactTable<TRow>({
    data: leafRows as TRow[],
    columns: tableColumns as ColumnDef<TRow>[],
    state: { columnVisibility },
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getRowId: modelRowId,
    autoResetPageIndex: false,
    autoResetExpanded: false,
  });
  const rowModels = table.getRowModel().rows;
  const rowModelsByScopeKey = React.useMemo(() => {
    const byScope = new Map<string, readonly TableRowModel<TRow>[]>();
    let offset = 0;
    for (const scope of leafScopes) {
      const count = leafResults.get(scope.key)?.rows.length ?? 0;
      byScope.set(scope.key, rowModels.slice(offset, offset + count));
      offset += count;
    }
    return byScope;
  }, [leafScopes, leafResults, rowModels]);

  const groupedItems = React.useMemo(
    () =>
      buildGroupedRenderModel<TRow>(
        groupByResults,
        leafResults,
        rowModelsByScopeKey,
        renderParams,
      ).items,
    [groupByResults, leafResults, rowModelsByScopeKey, renderParams],
  );

  const {
    visibleColumnCount,
    visibleFields,
    toggleVisibleField,
  } = useResourceViewTableChrome(table, columnVisibility);
  const tableScrollRef = React.useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: groupedItems.length,
    getScrollElement: () => tableScrollRef.current,
    initialRect: { width: 1024, height: 600 },
    estimateSize: (index) => estimateGroupedItemSize(groupedItems[index]),
    overscan: 10,
  });

  const rootResult = scopeModel.rootResult;
  const rootTotal = rootResult
    ? rootResult.totalCount ?? rootResult.buckets.length
    : undefined;
  const rootPageCount =
    rootTotal === undefined ? undefined : Math.max(1, Math.ceil(rootTotal / statePageSize));
  const list = React.useMemo<ResourceListResult>(
    () => ({
      rows: EMPTY_ARRAY,
      total: rootTotal,
      pageCount: rootPageCount,
      page: rootPage,
      pageSize: statePageSize,
      pageInfo: undefined,
      hasNext: rootPageCount !== undefined && rootPage < rootPageCount,
      hasPrev: rootPage > 1,
      setPage: resourceView.setPage,
      firstPage: () => resourceView.setPage(1),
      nextPage: () =>
        resourceView.setPage(
          rootPageCount ? Math.min(rootPage + 1, rootPageCount) : rootPage + 1,
        ),
      prevPage: () => resourceView.setPage(Math.max(1, rootPage - 1)),
      lastPage: () => {
        if (rootPageCount) resourceView.setPage(rootPageCount);
      },
      fetching: rootResult ? rootResult.fetching : true,
      error: errorFromUnknown(rootResult?.error ?? null),
      refetch: () => rootResult?.refetch(),
    }),
    [resourceView.setPage, rootResult, rootPage, rootPageCount, rootTotal, statePageSize],
  );
  const listState = useResourceRowsSnapshot<TRow>(list);

  return {
    list,
    listState,
    rows: EMPTY_ARRAY as readonly TRow[],
    requestedFields,
    mergedFilter,
    sortOrder,
    footerAggregate: grandTotal.aggregate,
    setScopePage,
    groupedItems,
    tableColumns: tableColumns as readonly ColumnDef<TRow>[],
    table,
    columnVisibility,
    visibleColumnCount,
    visibleFields,
    toggleVisibleField,
    rowModels,
    selectedIds: resourceView.state.selectedIds ?? EMPTY_SELECTED_IDS,
    pageIds: EMPTY_ARRAY,
    allPageSelected: false,
    somePageSelected: false,
    setPageSelection: () => undefined,
    groupedRows: EMPTY_ARRAY,
    listItems: EMPTY_ARRAY,
    expandedKeys,
    toggleGroup,
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
    () =>
      buildColumns(columns, {
        sort: resourceView.state.sort,
        setSort: resourceView.setSort,
      }),
    [columns, resourceView.state.sort, resourceView.setSort],
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
    footerAggregate: null,
    setScopePage: NOOP_SET_SCOPE_PAGE,
    groupedItems: EMPTY_ARRAY,
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
    footerAggregate: null,
    setScopePage: NOOP_SET_SCOPE_PAGE,
    groupedItems: EMPTY_ARRAY,
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
    () =>
      buildColumns(columns, {
        sort: resourceView.state.sort,
        setSort: resourceView.setSort,
      }),
    [columns, resourceView.state.sort, resourceView.setSort],
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

interface GroupedRenderParams {
  groupStack: readonly ResourceViewGroup[];
  baseFilter: ResourceViewFilter | undefined;
  expandedKeys: ReadonlySet<string>;
  pageByScope: Record<string, number>;
  rootPage: number;
  pageSize: number;
  queryMeasures: readonly GroupMeasure[];
  leafOrder: ListOrder | undefined;
  modelMetadata: ModelMetadata | null;
}

interface GroupedRenderModel<TRow extends Row> {
  groupScopes: GroupByBatchScope[];
  leafScopes: AngeeListBatchScope[];
  items: GroupedListItem<TRow>[];
  rootResult: UseAngeeGroupByResult | undefined;
}

/**
 * Walk the server group tree once, emitting the windowed `GroupedListItem`
 * stream and collecting the `_groups`/leaf scopes the batched hooks must fetch.
 * Pure: the same call yields the scope frontier (with empty leaf maps) and, once
 * the leaf rows resolve, the final render items (with the loaded row models). The
 * recursion descends only into expanded buckets whose parent level has resolved,
 * so the scope set grows one level at a time as parents arrive.
 */
function buildGroupedRenderModel<TRow extends Row>(
  groupByResults: ReadonlyMap<string, UseAngeeGroupByResult>,
  leafResults: ReadonlyMap<string, AngeeListBatchEntry>,
  rowModelsByScopeKey: ReadonlyMap<string, readonly TableRowModel<TRow>[]>,
  params: GroupedRenderParams,
): GroupedRenderModel<TRow> {
  const {
    groupStack,
    baseFilter,
    expandedKeys,
    pageByScope,
    rootPage,
    pageSize,
    queryMeasures,
    leafOrder,
    modelMetadata,
  } = params;
  const groupScopes: GroupByBatchScope[] = [];
  const leafScopes: AngeeListBatchScope[] = [];
  const items: GroupedListItem<TRow>[] = [];
  let rootResult: UseAngeeGroupByResult | undefined;

  const emitLeaf = (
    bucketKey: string,
    cumulativeFilter: ResourceViewFilter,
    bucket: AggregateBucket,
    label: string,
    depth: number,
  ): void => {
    const pageCount = Math.max(1, Math.ceil(bucket.count / GROUPED_LEAF_PAGE_SIZE));
    const currentPage = Math.min(pageByScope[bucketKey] ?? 1, pageCount);
    leafScopes.push({
      key: bucketKey,
      filter: cumulativeFilter,
      order: leafOrder,
      page: currentPage,
      pageSize: GROUPED_LEAF_PAGE_SIZE,
    });
    const leaf = leafResults.get(bucketKey);
    const rows = rowModelsByScopeKey.get(bucketKey) ?? EMPTY_ARRAY;
    // The sibling-list a record in this bucket opens into (detail prev/next).
    const nav: GroupedRecordNav = {
      filter: cumulativeFilter,
      order: leafOrder,
      page: currentPage,
      pageSize: GROUPED_LEAF_PAGE_SIZE,
      rows: leaf?.rows ?? EMPTY_ARRAY,
      total: leaf?.total,
      fetching: leaf?.fetching ?? false,
    };
    if (leaf?.error) {
      items.push({
        kind: "status",
        itemKey: `leaf-error:${bucketKey}`,
        depth,
        message: leaf.error.message,
        tone: "danger",
      });
    } else if ((!leaf || leaf.fetching) && rows.length === 0) {
      items.push({
        kind: "skeleton",
        itemKey: `leaf-skeleton:${bucketKey}`,
        depth,
        rowCount: Math.min(4, Math.max(1, bucket.count)),
      });
    } else if (rows.length === 0) {
      items.push({
        kind: "status",
        itemKey: `leaf-empty:${bucketKey}`,
        depth,
        message: "No records in this group.",
        tone: "muted",
      });
    } else {
      for (const row of rows) {
        items.push({ kind: "record", itemKey: `${bucketKey}:${row.id}`, row, nav });
      }
    }
    // The pager mirrors the original: shown once a page settles (hidden mid-fetch).
    if (leaf && !leaf.error && !leaf.fetching && bucket.count > 0) {
      items.push({
        kind: "pager",
        pageKey: bucketKey,
        depth,
        label,
        page: currentPage,
        pageSize: GROUPED_LEAF_PAGE_SIZE,
        total: bucket.count,
        unit: "records",
      });
    }
  };

  const walkLevel = (
    depth: number,
    parentFilter: ResourceViewFilter | undefined,
  ): void => {
    const axisGroup = groupStack[depth];
    if (!axisGroup) return;
    const dimension = resourceViewGroupToAggregateDimension(axisGroup, modelMetadata);
    const labelDimension = groupLabelDimension(axisGroup, modelMetadata);
    const dimensions: GroupByDimension[] = labelDimension
      ? [dimension, labelDimension]
      : [dimension];
    const hasuraDimensions = dimensions.map(hasuraGroupDimension);
    const orderBy = hasuraGroupOrderForDimensions(hasuraDimensions);
    const levelWhere = hasuraWhereFromCrudFilters(
      crudFiltersFromFilterRecord(parentFilter),
    );
    const levelScopeKey = stableSerialize({
      axis: dimension,
      filter: parentFilter ?? null,
      pageSize,
    });
    const storedPage = depth === 0 ? rootPage : pageByScope[levelScopeKey] ?? 1;
    const query: GroupByRequestOptions = {
      dimensions: hasuraDimensions,
      ...(orderBy ? { orderBy } : {}),
      ...(levelWhere !== undefined ? { where: levelWhere } : {}),
      measures: queryMeasures,
      page: storedPage,
      pageSize,
    };
    groupScopes.push({ key: levelScopeKey, query });
    const result = groupByResults.get(levelScopeKey);
    if (depth === 0) rootResult = result;

    if (!result || result.error || result.buckets.length === 0) {
      // Depth 0 defers its empty/loading/error states to the thin body (which owns
      // the `emptyMessage`); a nested level renders its own status inline.
      if (depth > 0) {
        if (result?.error) {
          items.push({
            kind: "status",
            itemKey: `error:${levelScopeKey}`,
            depth,
            message: result.error.message,
            tone: "danger",
          });
        } else if (!result || result.fetching) {
          items.push({
            kind: "skeleton",
            itemKey: `skeleton:${levelScopeKey}`,
            depth,
            rowCount: 4,
          });
        } else {
          items.push({
            kind: "status",
            itemKey: `empty:${levelScopeKey}`,
            depth,
            message: "No sub-groups.",
            tone: "muted",
          });
        }
      }
      return;
    }

    const levelTotal = result.totalCount ?? result.buckets.length;
    const isLeafLevel = depth === groupStack.length - 1;
    for (const bucket of result.buckets) {
      const bucketFilter = bucketFilterForGroup(bucket, axisGroup, modelMetadata);
      const expandable = bucketFilter !== undefined;
      const bucketKey = stableSerialize({
        scope: levelScopeKey,
        bucket: bucket.key ?? null,
      });
      const expanded = expandable && expandedKeys.has(bucketKey);
      const label = bucketLabel(bucket, axisGroup, modelMetadata);
      items.push({
        kind: "groupHeader",
        bucketKey,
        depth,
        label,
        count: bucket.count,
        expandable,
        expanded,
        bucket,
      });
      if (!expanded || bucketFilter === undefined) continue;
      const cumulativeFilter = Filter.combine(parentFilter ?? {}, bucketFilter);
      if (isLeafLevel) {
        emitLeaf(bucketKey, cumulativeFilter, bucket, label, depth);
      } else {
        walkLevel(depth + 1, cumulativeFilter);
      }
    }
    // Sub-group levels page within the body; depth 0 pages via the toolbar.
    if (depth > 0 && levelTotal > 0) {
      const pageCount = Math.max(1, Math.ceil(levelTotal / pageSize));
      items.push({
        kind: "pager",
        pageKey: levelScopeKey,
        depth,
        label: groupFieldLabel(axisGroup.field),
        page: Math.min(storedPage, pageCount),
        pageSize,
        total: levelTotal,
        unit: "groups",
      });
    }
  };

  walkLevel(0, baseFilter);
  return { groupScopes, leafScopes, items, rootResult };
}

function bucketLabel(
  bucket: AggregateBucket,
  group: ResourceViewGroup | undefined,
  metadata: ModelMetadata | null,
): string {
  if (!group) return "All records";
  const [label] = bucketValueLabels(bucket, [group], metadata);
  return label ?? "All records";
}

function groupScopesEqual(
  left: readonly GroupByBatchScope[],
  right: readonly GroupByBatchScope[],
): boolean {
  if (left === right) return true;
  if (left.length !== right.length) return false;
  return left.every((scope, index) => {
    const other = right[index];
    return (
      other !== undefined
      && scope.key === other.key
      && stableSerialize(scope.query) === stableSerialize(other.query)
    );
  });
}

function normaliseScopePage(page: number): number {
  if (!Number.isFinite(page)) return 1;
  return Math.max(1, Math.floor(page));
}

function requireGroupedDataResource(
  resourceId: string,
  metadata: ModelMetadata | null | undefined,
): NonNullable<ModelMetadata["resource"]> {
  const dataResource = metadata?.resource;
  if (!dataResource) {
    throw new Error(`Resource "${resourceId}" has no data resource metadata.`);
  }
  return dataResource;
}

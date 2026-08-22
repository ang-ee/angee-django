import * as React from "react";
import {
  refineResourceName,
  type ModelMetadata,
  type Row,
} from "@angee/metadata";
import { useTable as useRefineTable } from "@refinedev/react-table";
import {
  useList,
  type BaseRecord,
  type HttpError,
  type MetaQuery,
} from "@refinedev/core";
import {
  functionalUpdate,
  getCoreRowModel,
  getExpandedRowModel,
  getFilteredRowModel,
  getGroupedRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type ExpandedState,
  type FilterFn,
  type OnChangeFn,
  type PaginationState,
  type Row as TableRowModel,
  type RowSelectionState,
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
  stableSerialize,
  useAngeeAggregate,
  useAngeeGroupByBatch,
  useAngeeListBatch,
  type AggregateBucket,
  type AngeeListBatchEntry,
  type GroupByBatchScope,
} from "@angee/refine";

import { errorFromUnknown } from "../data/errors";
import { useUiT } from "../i18n";
import type { ResourceViewContextValue } from "./resource-view-context";
import {
  DEFAULT_TEXT_FILTER_FIELD,
  Filter,
  type ResourceListOrder,
  type ResourceViewFilter,
  type ResourceViewGroup,
} from "./resource-view-model";
import {
  GROUP_ROW_HEIGHT,
  RECORD_ROW_HEIGHT,
  buildColumns,
  estimateGroupedItemSize,
  groupFieldLabel,
  groupMeasuresFromColumns,
  hasuraMeasuresFromGroupMeasures,
  isGroupingOnlyColumn,
  withGroupingOnlyColumnsHidden,
  tableColumnLabel,
  type GroupedListItem,
  type RowGroup,
  type VisibleFieldOption,
} from "./resource-view-list-body";
import type { ColumnDescriptor } from "./page";
import {
  listBatchTarget,
  requireDataResource,
  useAggregateOperation,
  useGroupOperation,
} from "./resource-operations";
import {
  rowGroupsFromLaneSource,
  useBoardLaneState,
  type BoardLaneState,
  type ResolvedBoardLaneSource,
} from "./resource-view-board-lanes";
import {
  defaultResourceOrder,
  groupingStateFromResourceGroups,
  idsFromRowSelectionState,
  leafTableRows,
  modelRowId,
  requestedFieldPaths,
  rowGroupsFromTableRows,
  rowSelectionStateFromIds,
  sortingStateFromResourceSort,
  stringRowId,
} from "./resource-view-codecs";
import {
  resourceViewFilterFn,
  type LocalFilterState,
} from "./resource-view-client-filter";
import {
  buildGroupedRenderModel,
  groupedPageWindow,
  groupScopesEqual,
  normaliseScopePage,
  type GroupedRenderParams,
} from "./resource-view-grouped-model";
import type { BoardCardPlacement } from "./resource-view-types";

type RowRecord = BaseRecord & Row;
type ResourceFilterInput = Record<string, unknown>;

export type StringIdRow = Row & { id: string };

export interface ResourceListSnapshot<TRow extends Row = Row> {
  rows: readonly TRow[];
  total: number | undefined;
  page: number;
  pageSize: number;
  pageCount: number | undefined;
  hasNext: boolean;
  hasPrev: boolean;
  fetching: boolean;
  error?: Error | null;
  navigationScope?: ListViewNavigationScope;
}

export interface ListViewNavigationScope {
  filter: ResourceViewFilter | undefined;
  order: ResourceListOrder | undefined;
  page: number;
  pageSize: number;
}

export interface UseResourceViewSurfaceProps<TRow extends Row = Row> {
  resource: string;
  columns: readonly ColumnDescriptor<TRow>[];
  fields?: readonly string[];
  filter?: ResourceFilterInput;
  order?: ResourceListOrder;
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly ResourceViewGroup[];
  laneSource?: ResolvedBoardLaneSource | null;
  enabled?: boolean;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

export interface UseRowsResourceViewSurfaceProps<
  TRow extends StringIdRow = StringIdRow,
> {
  rows: readonly TRow[];
  columns: readonly ColumnDescriptor<TRow>[];
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
  groupStack?: readonly ResourceViewGroup[];
  fetching?: boolean;
  error?: Error | null;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
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
  tableScrollRef: React.RefObject<HTMLDivElement | null>;
  rowVirtualizer: Virtualizer<HTMLDivElement, Element>;
}

interface FlatResourceViewPresentationSurface<TRow extends Row = Row>
  extends ResourceViewPresentationSurface<TRow> {
  pageIds: readonly string[];
  allPageSelected: boolean;
  somePageSelected: boolean;
  setPageSelection: (checked: boolean) => void;
  groupedRows: readonly RowGroup<TRow>[];
  boardDragEnabled: boolean;
  boardRankField?: string;
  boardOptimisticPlacementByRowId: ReadonlyMap<string, BoardCardPlacement>;
  onBoardCardMove?: (
    row: TRow,
    laneId: string | null,
    rank?: number,
  ) => void | Promise<void>;
}

interface ResourceViewSurfaceBase<TRow extends Row = Row> {
  list: ResourceListResult;
  listState: ResourceListSnapshot<TRow>;
  rows: readonly TRow[];
  requestedFields: readonly string[];
  mergedFilter: ResourceViewFilter | undefined;
  sortOrder: ResourceListOrder | undefined;
}

export interface ResourceViewSurface<TRow extends Row = Row>
  extends ResourceViewSurfaceBase<TRow>,
    FlatResourceViewPresentationSurface<TRow> {
  kind: "flat";
}

/** Fields owned only by the server-grouped render stream. */
export interface GroupedResourceViewSurface<TRow extends Row = Row>
  extends ResourceViewSurfaceBase<TRow>,
    ResourceViewPresentationSurface<TRow> {
  kind: "grouped";
  /** Grand-total measure footer for the grouped result. */
  footerAggregate: AggregateBucket | null;
  /** Set a server-grouped sub-group/leaf scope's page. */
  setScopePage: (key: string, page: number) => void;
  /** The windowed server-grouped render stream. */
  groupedItems: readonly GroupedListItem<TRow>[];
  /** Server `_groups` bucket expansion keys. */
  expandedKeys: ReadonlySet<string>;
  toggleGroup: (key: string) => void;
}

const EMPTY_ARRAY = [] as const;
const EMPTY_BOARD_PLACEMENTS: ReadonlyMap<string, BoardCardPlacement> = new Map();
const EMPTY_SELECTED_IDS: ReadonlySet<string> = new Set();
const EMPTY_EXPANDED_KEYS: ReadonlySet<string> = new Set();
const EMPTY_LEAF_RESULTS: ReadonlyMap<string, AngeeListBatchEntry> = new Map();

export interface RowsResourceViewSurface<TRow extends StringIdRow = StringIdRow>
  extends FlatResourceViewPresentationSurface<TRow> {
  kind: "flat";
  list: ResourceListSnapshot<TRow>;
  listState: ResourceListSnapshot<TRow>;
  rows: readonly TRow[];
  sourceRows: readonly TRow[];
}

interface ResourceRowsSnapshotSource {
  rows: readonly Row[];
  total: number | undefined;
  page: number;
  pageSize: number;
  pageCount: number | undefined;
  hasNext: boolean;
  hasPrev: boolean;
  fetching: boolean;
  error: Error | null;
}

interface UseResourceRowsSnapshotOptions<TRow extends Row> {
  navigation?: Pick<ListViewNavigationScope, "filter" | "order">;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

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

function listResultFromPageState<TRow extends Row>({
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
function useResourceViewTableState<TRow extends Row>({
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
  resourceView,
  modelMetadata = null,
  groupStack,
  laneSource,
  onListStateChange,
}: UseResourceViewSurfaceProps<TRow>): GroupedResourceViewSurface<TRow> {
  const t = useUiT();
  const dataResource = requireDataResource(resource, modelMetadata);
  const aggregateOperation = useAggregateOperation(dataResource);
  const groupOperation = useGroupOperation(dataResource);
  const listTarget = listBatchTarget(dataResource);

  const { requestedFields, mergedFilter, sortOrder } = useResourceViewQueryFacts({
    columns,
    fields,
    filter,
    order,
    resourceView,
    modelMetadata,
    laneSource,
  });
  const leafOrder = React.useMemo<ResourceListOrder | undefined>(
    () => sortOrder ?? order,
    [sortOrder, order],
  );
  const rowGroupStack = groupStack ?? resourceView.state.groupStack;
  const rootPage = resourceView.state.page;
  const statePageSize = resourceView.state.pageSize;

  // Shared table state plus per-group/footer measures.
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
  } = tableState;
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
  const grandTotal = useAngeeAggregate(aggregateOperation.target, {
    document: aggregateOperation.document,
    where,
    measures: queryMeasures,
    enabled: rowGroupStack.length > 0 && measures.length > 0,
  });

  // Collapse state and per-scope pager pages (one map, keyed by cumulative scope).
  const [expandedKeys, setExpandedKeys] =
    React.useState<ReadonlySet<string>>(EMPTY_EXPANDED_KEYS);
  const toggleGroup = React.useCallback((key: string) => {
    setExpandedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
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
      emptyGroupMessage: t("list.emptyGroup"),
      emptySubgroupsMessage: t("list.emptySubgroups"),
      emptyValueLabel: t("list.emptyValue"),
      emptyRelationLabel: (field) =>
        t("list.emptyRelation", {
          relation: (
            modelMetadata?.fields[field]?.label ?? groupFieldLabel(field)
          ).toLocaleLowerCase(),
        }),
      allRecordsLabel: t("list.allRecords"),
      t,
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
      t,
    ],
  );

  // Per-level `_groups` requests stage over renders: the desired scope frontier is
  // derived from the resolved buckets, so it grows one level deeper each time a
  // parent resolves. `useAngeeGroupByBatch` is a single hook, so a dynamic-length
  // array is rules-of-hooks safe.
  const [groupScopes, setGroupScopes] =
    React.useState<readonly GroupByBatchScope[]>(EMPTY_ARRAY);
  const groupByResults = useAngeeGroupByBatch(groupOperation.target, groupScopes, {
    document: groupOperation.document,
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
  const leafResults = useAngeeListBatch(listTarget, leafScopes, {
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
    state: { columnVisibility: effectiveColumnVisibility },
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
  const rootWindow = rootResult
    ? groupedPageWindow(rootResult, rootPage, statePageSize)
    : undefined;
  const rootTotal = rootWindow?.total;
  const rootPageCount =
    rootTotal === undefined ? undefined : Math.max(1, Math.ceil(rootTotal / statePageSize));
  React.useEffect(() => {
    if (
      rootResult
      && !rootResult.fetching
      && rootPageCount !== undefined
      && rootPage > rootPageCount
    ) {
      resourceView.setPage(rootPageCount);
    }
  }, [resourceView.setPage, rootPage, rootPageCount, rootResult]);
  const list = React.useMemo<ResourceListResult>(
    () =>
      listResultFromPageState({
        resourceView,
        error: rootResult?.error ?? null,
        fetching: rootResult ? rootResult.fetching : true,
        refetch: () => rootResult?.refetch(),
        rows: EMPTY_ARRAY,
        total: rootTotal,
        page: rootPage,
        pageSize: statePageSize,
        pageCount: rootPageCount,
        hasNext: rootWindow?.hasNext ?? false,
      }),
    [
      resourceView,
      rootResult,
      rootPage,
      rootPageCount,
      rootTotal,
      rootWindow?.hasNext,
      statePageSize,
    ],
  );
  const listState = useResourceRowsSnapshot<TRow>(list, {
    navigation: { filter: mergedFilter, order: sortOrder },
    onListStateChange,
  });
  // Publish the snapshot like the flat surface: rows are empty here (the grouped
  // render stream owns the visible records), but the non-null `navigationScope`
  // carries the folded scope's own filter/order. Without this, a record pager
  // built by `useListRecordNavigation` would retain a stale flat snapshot when a
  // grouped scope (e.g. storage's default folder-grouped "All files") becomes
  // active; a hidden replay list then pages the folded scope, not a prior folder.
  return {
    kind: "grouped",
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
  const refineFiltersKey = React.useMemo(
    () => stableSerialize(refineFilters),
    [refineFilters],
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
  React.useEffect(() => {
    tableResult.refineCore.setFilters([], "replace");
  }, [refineFiltersKey, tableResult.refineCore.setFilters]);
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
    resourceView,
    modelMetadata,
    laneSource,
    includeDeclaredOrder: false,
  });
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
  const refetchRows = React.useCallback(() => {
    void run.query.refetch();
  }, [run.query.refetch]);
  const boardLaneState = useBoardLaneState<TRow>({
    laneSource,
    modelMetadata,
    rows: allRows,
    enabled: active && resourceView.state.view === "board",
    refetchRows,
  });
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

  const fetching = run.query.isFetching || boardLaneState.fetching;
  const error = errorFromUnknown(run.query.error);
  const refetch = React.useCallback(() => {
    void run.query.refetch();
  }, [run.query]);
  const presentation = useResourceViewPresentationSurface<TRow>({
    rows: allRows,
    columns,
    resourceView,
    modelMetadata,
    groupStack,
    boardLaneState,
    getRowId: modelRowId,
    filter: mergedFilter,
  });
  const pageRows = React.useMemo(
    () => leafTableRows(presentation.rowModels).map((row) => row.original),
    [presentation.rowModels],
  );
  const filteredTotal = presentation.table.getFilteredRowModel().rows.length;
  const pageCount = Math.max(1, presentation.table.getPageCount());
  const list = React.useMemo<ResourceListResult>(
    () =>
      listResultFromPageState({
        resourceView,
        error,
        fetching,
        refetch,
        rows: pageRows,
        total: filteredTotal,
        pageCount,
      }),
    [
      error,
      fetching,
      filteredTotal,
      pageCount,
      pageRows,
      refetch,
      resourceView,
    ],
  );
  const listState = useResourceRowsSnapshot<TRow>(list, { onListStateChange });

  return {
    kind: "flat",
    list,
    listState,
    rows: pageRows,
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
  resourceView,
  modelMetadata = null,
  groupStack,
  fetching = false,
  error = null,
  onListStateChange,
}: UseRowsResourceViewSurfaceProps<TRow>): RowsResourceViewSurface<TRow> {
  const textSearchFields = React.useMemo(
    () => columns.map((column) => column.field),
    [columns],
  );
  const presentation = useResourceViewPresentationSurface({
    rows,
    columns,
    resourceView,
    filter: resourceView.state.filter,
    textSearchField: DEFAULT_TEXT_FILTER_FIELD,
    textSearchFields,
    modelMetadata,
    groupStack,
    getRowId: stringRowId,
  });
  const pageRows = React.useMemo(
    () => leafTableRows(presentation.rowModels).map((row) => row.original),
    [presentation.rowModels],
  );
  const total = presentation.table.getFilteredRowModel().rows.length;
  const pageCount = Math.max(1, presentation.table.getPageCount());

  const listState = useResourceRowsSnapshot<TRow>({
    rows: pageRows,
    total,
    page: resourceView.state.page,
    pageSize: resourceView.state.pageSize,
    pageCount,
    hasNext: resourceView.state.page < pageCount,
    hasPrev: resourceView.state.page > 1,
    fetching,
    error,
  }, { onListStateChange });

  return {
    kind: "flat",
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

function useResourceViewPresentationSurfaceFromTable<TRow extends Row>({
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

function useResourceViewTableChrome<TRow extends Row>(
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

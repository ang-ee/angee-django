import * as React from "react";
import { type Row } from "@angee/metadata";
import { getCoreRowModel, useReactTable, type ColumnDef, type Row as TableRowModel } from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { crudFiltersFromFilterRecord, hasuraWhereFromCrudFilters, stableSerialize, useAngeeAggregate, useAngeeGroupByBatch, useAngeeListBatch, type GroupByBatchScope } from "@angee/refine";
import { useUiT } from "../../../i18n";
import { type ResourceListOrder } from "../resource-view-model";
import { estimateGroupedItemSize, groupFieldLabel, groupMeasuresFromColumns, hasuraMeasuresFromGroupMeasures } from "../resource-view-list-body";
import { listBatchTarget, requireDataResource, useAggregateOperation, useGroupOperation } from "../resource-operations";
import { modelRowId } from "../resource-view-codecs";
import { buildGroupedRenderModel, groupedPageWindow, groupScopesEqual, normaliseScopePage, type GroupedRenderParams } from "../resource-view-grouped-model";
import { useResourceViewTableChrome } from "./presentation";
import { listResultFromPageState, useResourceRowsSnapshot, useResourceViewQueryFacts, useResourceViewTableState } from "./table-state";
import { EMPTY_ARRAY, EMPTY_EXPANDED_KEYS, EMPTY_LEAF_RESULTS, EMPTY_SELECTED_IDS } from "./types";
import type { GroupedResourceViewSurface, ResourceListResult, UseResourceViewSurfaceProps } from "./types";
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
  defaultExpandedGroups = "all",
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

  // Expansion belongs to the active grouping axis. Root buckets discovered in
  // later pages auto-expand under the default "all" policy unless the user has
  // explicitly collapsed that bucket; nested groups remain user-driven.
  const expansionAxisKey = React.useMemo(
    () => stableSerialize([rowGroupStack, defaultExpandedGroups]),
    [defaultExpandedGroups, rowGroupStack],
  );
  const [expansionState, setExpansionState] =
    React.useState<GroupExpansionState>(() => emptyGroupExpansion(expansionAxisKey));
  const activeExpansion =
    expansionState.axisKey === expansionAxisKey
      ? expansionState
      : emptyGroupExpansion(expansionAxisKey);
  const expandedKeys = React.useMemo(
    () => effectiveExpandedKeys(activeExpansion),
    [activeExpansion],
  );
  const toggleGroup = React.useCallback((key: string) => {
    setExpansionState((current) => {
      const base =
        current.axisKey === expansionAxisKey
          ? current
          : emptyGroupExpansion(expansionAxisKey);
      const currentlyExpanded =
        !base.collapsedKeys.has(key)
        && (base.defaultExpandedKeys.has(key) || base.explicitExpandedKeys.has(key));
      const collapsedKeys = new Set(base.collapsedKeys);
      const explicitExpandedKeys = new Set(base.explicitExpandedKeys);
      if (currentlyExpanded) {
        collapsedKeys.add(key);
        explicitExpandedKeys.delete(key);
      } else {
        collapsedKeys.delete(key);
        explicitExpandedKeys.add(key);
      }
      return { ...base, collapsedKeys, explicitExpandedKeys };
    });
  }, [expansionAxisKey]);
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
  const rootBucketKeys = React.useMemo(
    () =>
      scopeModel.items.flatMap((item) =>
        item.kind === "groupHeader" && item.depth === 0
          ? [item.bucketKey]
          : [],
      ),
    [scopeModel.items],
  );
  React.useEffect(() => {
    if (defaultExpandedGroups !== "all" || rootBucketKeys.length === 0) return;
    setExpansionState((current) => {
      const base =
        current.axisKey === expansionAxisKey
          ? current
          : emptyGroupExpansion(expansionAxisKey);
      const defaultExpandedKeys = new Set(base.defaultExpandedKeys);
      let changed = current.axisKey !== expansionAxisKey;
      for (const key of rootBucketKeys) {
        if (base.collapsedKeys.has(key) || defaultExpandedKeys.has(key)) continue;
        defaultExpandedKeys.add(key);
        changed = true;
      }
      return changed ? { ...base, defaultExpandedKeys } : current;
    });
  }, [defaultExpandedGroups, expansionAxisKey, rootBucketKeys]);
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

interface GroupExpansionState {
  axisKey: string;
  collapsedKeys: ReadonlySet<string>;
  explicitExpandedKeys: ReadonlySet<string>;
  defaultExpandedKeys: ReadonlySet<string>;
}

function emptyGroupExpansion(axisKey: string): GroupExpansionState {
  return {
    axisKey,
    collapsedKeys: EMPTY_EXPANDED_KEYS,
    explicitExpandedKeys: EMPTY_EXPANDED_KEYS,
    defaultExpandedKeys: EMPTY_EXPANDED_KEYS,
  };
}

function effectiveExpandedKeys(state: GroupExpansionState): ReadonlySet<string> {
  if (
    state.defaultExpandedKeys.size === 0
    && state.explicitExpandedKeys.size === 0
  ) {
    return EMPTY_EXPANDED_KEYS;
  }
  const expanded = new Set(state.defaultExpandedKeys);
  for (const key of state.explicitExpandedKeys) expanded.add(key);
  for (const key of state.collapsedKeys) expanded.delete(key);
  return expanded;
}

import type { ModelMetadata, Row } from "@angee/metadata";
import type { PaginationState, Row as TableRowModel } from "@tanstack/react-table";
import {
  crudFiltersFromFilterRecord,
  hasuraWhereFromCrudFilters,
  stableSerialize,
  type AggregateBucket,
  type AngeeListBatchEntry,
  type AngeeListBatchScope,
  type GroupByBatchScope,
  type GroupByRequestOptions,
  type GroupByResult,
  type UseAngeeGroupByResult,
} from "@angee/refine";

import {
  bucketFilterForGroup,
  bucketValueLabels,
  groupLabelDimension,
  hasuraGroupDimension,
  hasuraGroupOrderForDimensions,
  resourceViewGroupToAggregateDimension,
  type GroupByDimension,
  type GroupedListItem,
  type GroupedListPager,
  type GroupedRecordNav,
  type GroupMeasure,
} from "./resource-view-list-body";
import {
  Filter,
  type ResourceListOrder,
  type ResourceViewFilter,
  type ResourceViewGroup,
} from "./resource-view-model";
import type { UiTranslate } from "../../i18n";

/** Leaf record page size inside a server-grouped bucket. */
const GROUPED_LEAF_PAGE_SIZE = 20;
const EMPTY_ARRAY = [] as const;

export interface GroupedRenderParams {
  groupStack: readonly ResourceViewGroup[];
  baseFilter: ResourceViewFilter | undefined;
  expandedKeys: ReadonlySet<string>;
  paginationByScope: Readonly<Record<string, PaginationState>>;
  rootPage: number;
  pageSize: number;
  queryMeasures: readonly GroupMeasure[];
  leafOrder: ResourceListOrder | undefined;
  modelMetadata: ModelMetadata | null;
  emptyGroupMessage: string;
  emptySubgroupsMessage: string;
  emptyValueLabel: string;
  emptyRelationLabel: (field: string) => string;
  allRecordsLabel: string;
  t: UiTranslate;
}

export interface GroupedRenderModel<TRow extends Row> {
  groupScopes: GroupByBatchScope[];
  leafScopes: AngeeListBatchScope[];
  items: GroupedListItem<TRow>[];
  rootResult: UseAngeeGroupByResult | undefined;
}

/**
 * Walk the server group tree once, collecting the request frontier and emitting
 * the windowed render stream. The function is pure; it can run before and after
 * leaf records resolve without acquiring data itself.
 */
export function buildGroupedRenderModel<TRow extends Row>(
  groupByResults: ReadonlyMap<string, UseAngeeGroupByResult>,
  leafResults: ReadonlyMap<string, AngeeListBatchEntry>,
  rowModelsByScopeKey: ReadonlyMap<string, readonly TableRowModel<TRow>[]>,
  params: GroupedRenderParams,
): GroupedRenderModel<TRow> {
  const {
    groupStack,
    baseFilter,
    expandedKeys,
    paginationByScope,
    rootPage,
    pageSize,
    queryMeasures,
    leafOrder,
    modelMetadata,
    emptyGroupMessage,
    emptySubgroupsMessage,
    emptyValueLabel,
    emptyRelationLabel,
    allRecordsLabel,
    t,
  } = params;
  const groupScopes: GroupByBatchScope[] = [];
  const leafScopes: AngeeListBatchScope[] = [];
  const items: GroupedListItem<TRow>[] = [];
  let rootResult: UseAngeeGroupByResult | undefined;

  const emitLeaf = (
    bucketKey: string,
    cumulativeFilter: ResourceViewFilter,
    bucket: AggregateBucket,
    depth: number,
  ): GroupedListPager => {
    const pagination = paginationByScope[bucketKey];
    const leafPageSize = pagination?.pageSize ?? GROUPED_LEAF_PAGE_SIZE;
    const pageCount = Math.max(1, Math.ceil(bucket.count / leafPageSize));
    const currentPage = Math.min((pagination?.pageIndex ?? 0) + 1, pageCount);
    leafScopes.push({
      key: bucketKey,
      filter: cumulativeFilter,
      order: leafOrder,
      page: currentPage,
      pageSize: leafPageSize,
    });
    const leaf = leafResults.get(bucketKey);
    const rows = rowModelsByScopeKey.get(bucketKey) ?? EMPTY_ARRAY;
    const nav: GroupedRecordNav = {
      filter: cumulativeFilter,
      order: leafOrder,
      page: currentPage,
      pageSize: leafPageSize,
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
        message: emptyGroupMessage,
        tone: "muted",
      });
    } else {
      for (const row of rows) {
        items.push({ kind: "record", itemKey: `${bucketKey}:${row.id}`, row, nav });
      }
    }
    return {
      pageKey: bucketKey,
      page: currentPage,
      pageSize: leafPageSize,
      total: bucket.count,
      unit: "records",
      pending: !leaf || leaf.fetching || Boolean(leaf.error),
    };
  };

  const walkLevel = (
    depth: number,
    parentFilter: ResourceViewFilter | undefined,
  ): GroupedListPager | undefined => {
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
    });
    const pagination = paginationByScope[levelScopeKey];
    const levelPageSize = depth === 0 ? pageSize : pagination?.pageSize ?? pageSize;
    const storedPage = depth === 0 ? rootPage : (pagination?.pageIndex ?? 0) + 1;
    const query: GroupByRequestOptions = {
      dimensions: hasuraDimensions,
      ...(orderBy ? { orderBy } : {}),
      ...(levelWhere !== undefined ? { where: levelWhere } : {}),
      measures: queryMeasures,
      page: storedPage,
      pageSize: levelPageSize,
    };
    groupScopes.push({ key: levelScopeKey, query });
    const result = groupByResults.get(levelScopeKey);
    if (depth === 0) rootResult = result;
    const pager: GroupedListPager = {
      pageKey: levelScopeKey,
      page: result && !result.error
        ? Math.min(storedPage, Math.max(1, Math.ceil(result.totalCount / levelPageSize)))
        : storedPage,
      pageSize: levelPageSize,
      total: result?.error ? undefined : result?.totalCount,
      unit: "groups",
      pending: !result || result.fetching || Boolean(result.error),
    };

    if (!result || result.error || result.buckets.length === 0) {
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
            message: emptySubgroupsMessage,
            tone: "muted",
          });
        }
      }
      return pager;
    }

    const isLeafLevel = depth === groupStack.length - 1;
    for (const bucket of result.buckets) {
      const bucketFilter = bucketFilterForGroup(bucket, axisGroup, modelMetadata);
      const expandable = bucketFilter !== undefined;
      const bucketKey = stableSerialize({
        scope: levelScopeKey,
        bucket: bucket.key ?? null,
      });
      const expanded = expandable && expandedKeys.has(bucketKey);
      const label = bucketLabel(
        bucket,
        axisGroup,
        modelMetadata,
        allRecordsLabel,
        emptyValueLabel,
        t,
        emptyRelationLabel,
      );
      const header: Extract<GroupedListItem<TRow>, { kind: "groupHeader" }> = {
        kind: "groupHeader",
        bucketKey,
        depth,
        label,
        count: bucket.count,
        expandable,
        expanded,
        bucket,
      };
      items.push(header);
      if (!expanded || bucketFilter === undefined) continue;
      const cumulativeFilter = Filter.combine(parentFilter ?? {}, bucketFilter);
      header.pager = isLeafLevel
        ? emitLeaf(bucketKey, cumulativeFilter, bucket, depth)
        : walkLevel(depth + 1, cumulativeFilter);
    }
    return pager;
  };

  walkLevel(0, baseFilter);
  return { groupScopes, leafScopes, items, rootResult };
}

function bucketLabel(
  bucket: AggregateBucket,
  group: ResourceViewGroup | undefined,
  metadata: ModelMetadata | null,
  allRecordsLabel: string,
  emptyValueLabel: string,
  t: UiTranslate,
  emptyRelationLabel: (field: string) => string,
): string {
  if (!group) return allRecordsLabel;
  const [label] = bucketValueLabels(
    bucket,
    [group],
    metadata,
    emptyValueLabel,
    t,
    emptyRelationLabel,
  );
  return label ?? allRecordsLabel;
}

export function groupScopesEqual(
  left: readonly GroupByBatchScope[],
  right: readonly GroupByBatchScope[],
): boolean {
  if (left === right) return true;
  if (left.length !== right.length) return false;
  return left.every((scope, index) => {
    const other = right[index];
    return other !== undefined
      && scope.key === other.key
      && stableSerialize(scope.query) === stableSerialize(other.query);
  });
}

export function normaliseScopePage(page: number): number {
  if (!Number.isFinite(page)) return 1;
  return Math.max(1, Math.floor(page));
}

export function groupedPageWindow(
  result: GroupByResult,
  page: number,
  pageSize: number,
): { total: number; hasNext: boolean } {
  return {
    total: result.totalCount,
    hasNext: page * pageSize < result.totalCount,
  };
}

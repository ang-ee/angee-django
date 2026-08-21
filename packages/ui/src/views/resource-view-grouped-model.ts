import type { ModelMetadata, Row } from "@angee/metadata";
import type { Row as TableRowModel } from "@tanstack/react-table";
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
  groupFieldLabel,
  groupLabelDimension,
  hasuraGroupDimension,
  hasuraGroupOrderForDimensions,
  resourceViewGroupToAggregateDimension,
  type GroupByDimension,
  type GroupedListItem,
  type GroupedRecordNav,
  type GroupMeasure,
} from "./resource-view-list-body";
import {
  Filter,
  type ResourceListOrder,
  type ResourceViewFilter,
  type ResourceViewGroup,
} from "./resource-view-model";

/** Leaf record page size inside a server-grouped bucket. */
const GROUPED_LEAF_PAGE_SIZE = 20;
const EMPTY_ARRAY = [] as const;

export interface GroupedRenderParams {
  groupStack: readonly ResourceViewGroup[];
  baseFilter: ResourceViewFilter | undefined;
  expandedKeys: ReadonlySet<string>;
  pageByScope: Record<string, number>;
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
    pageByScope,
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
        message: emptyGroupMessage,
        tone: "muted",
      });
    } else {
      for (const row of rows) {
        items.push({ kind: "record", itemKey: `${bucketKey}:${row.id}`, row, nav });
      }
    }
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
      return;
    }

    const levelTotal = result.totalCount;
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
        emptyRelationLabel,
      );
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
      if (isLeafLevel) emitLeaf(bucketKey, cumulativeFilter, bucket, label, depth);
      else walkLevel(depth + 1, cumulativeFilter);
    }
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
  allRecordsLabel: string,
  emptyValueLabel: string,
  emptyRelationLabel: (field: string) => string,
): string {
  if (!group) return allRecordsLabel;
  const [label] = bucketValueLabels(
    bucket,
    [group],
    metadata,
    emptyValueLabel,
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

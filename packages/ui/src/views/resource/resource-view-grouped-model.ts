import { ResourceQuery, type ModelMetadata, type Row } from "@angee/metadata";
import type { PaginationState, Row as TableRowModel } from "@tanstack/react-table";
import {
  clampPageSize,
  stableSerialize,
  type AggregateBucket,
  type AngeeListBatchEntry,
  type GroupByBatchScope,
  type GroupByRequestOptions,
  type GroupByResult,
  type UseAngeeGroupByResult,
} from "@angee/refine";

import {
  bucketValueLabels,
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
import { errorFromUnknown } from "../../data/errors";
import type { CollectionGroupRequest } from "./collection-source";
import type { SelectedGroup, SelectionGroupPath, SelectionPlace } from "./resource-view-types";

/** Default size of a server-grouped bucket's first page; a source may declare its own. */
const GROUPED_LEAF_PAGE_SIZE = 20;
const EMPTY_ARRAY = [] as const;

export interface GroupedRenderParams {
  query?: ResourceQuery;
  /**
   * The request each groups result answers, by scope key. A level's results are
   * keyed without its page, so after a page change they answer the previous page
   * until the batch catches up: a level lists its groups only from the answer to its
   * own request. Absent: every result answers its level's request.
   */
  requested?: ReadonlyMap<string, GroupByRequestOptions>;
  leafPageSize?: number;
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
  emptyRelationLabel: (field: string) => string;
  allRecordsLabel: string;
  t: UiTranslate;
}

/** Semantic leaf scope; the surface projects it once before transport. */
export interface GroupedLeafScope {
  key: string;
  filter: ResourceViewFilter;
  order: ResourceListOrder | undefined;
  page: number;
  pageSize: number;
}

export interface GroupedRenderModel<TRow extends Row> {
  groupScopes: GroupedGroupScope[];
  /** Leaf pages to request: a later or resized page of one group. */
  leafScopes: GroupedLeafScope[];
  /** Every expanded leaf bucket, in display order. */
  leafKeys: string[];
  /** First pages the groups read carried, keyed like `leafScopes`. */
  carriedLeaves: ReadonlyMap<string, AngeeListBatchEntry>;
  items: GroupedListItem<TRow>[];
  rootResult: GroupedQueryResult | undefined;
  shown: GroupedRenderShown;
}

/** What a grouped render shows, for checking a selection's captured places against it. */
export interface GroupedRenderShown {
  /**
   * Every page the render shows, by key: the bucket keys a level's page of groups
   * lists, or the row ids a group's page of records lists, as the page last settled;
   * a page that refetches keeps them until it settles again. Null until the page has
   * settled once, or after it failed. A page the render does not show is absent.
   */
  pages: ReadonlyMap<string, ReadonlySet<string> | null>;
  /** Each group's position among its level's groups, where a settled page lists it. */
  positions: ReadonlyMap<string, number>;
}

/** Native and authored transports expose the same server group facts. */
export interface GroupedQueryResult extends GroupByResult {
  summary?: string;
  fetching: boolean;
  error: Error | UseAngeeGroupByResult["error"];
  /** When the read last settled; null until it has, so a refetch keeps its groups and a first load has none. */
  updatedAt: UseAngeeGroupByResult["updatedAt"];
  refetch: () => void;
}

export interface GroupedGroupScope
  extends GroupByBatchScope,
    CollectionGroupRequest {}

/**
 * Walk the server group tree once, collecting the request frontier and emitting
 * the windowed render stream. The function is pure; it can run before and after
 * leaf records resolve without acquiring data itself.
 */
export function buildGroupedRenderModel<TRow extends Row>(
  groupByResults: ReadonlyMap<string, GroupedQueryResult>,
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
    emptyRelationLabel,
    allRecordsLabel,
    t,
  } = params;
  const groupScopes: GroupedGroupScope[] = [];
  const leafScopes: GroupedLeafScope[] = [];
  const leafKeys: string[] = [];
  const carriedLeaves = new Map<string, AngeeListBatchEntry>();
  const items: GroupedListItem<TRow>[] = [];
  const pages = new Map<string, Set<string> | null>();
  const positions = new Map<string, number>();
  let rootResult: GroupedQueryResult | undefined;
  const resourceQuery =
    params.query ?? (modelMetadata ? ResourceQuery.from(modelMetadata) : null);
  // The one owner of a group's first-page size: the groups read carries that page.
  const declaredLeafPageSize = clampPageSize(params.leafPageSize ?? GROUPED_LEAF_PAGE_SIZE);

  const emitLeaf = (
    bucketKey: string,
    cumulativeFilter: ResourceViewFilter,
    bucket: AggregateBucket,
    depth: number,
    groups: GroupedQueryResult,
    groupPath: SelectionGroupPath,
  ): GroupedListPager => {
    const pagination = paginationByScope[bucketKey];
    const leafPageSize = pagination?.pageSize ?? declaredLeafPageSize;
    const pageCount = Math.max(1, Math.ceil(bucket.count / leafPageSize));
    const currentPage = Math.min((pagination?.pageIndex ?? 0) + 1, pageCount);
    leafKeys.push(bucketKey);
    if (currentPage === 1 && leafPageSize === declaredLeafPageSize) {
      // Page 1 at the declared size comes with the groups read; a group without
      // it is a broken groups contract, never a silent per-group request.
      carriedLeaves.set(bucketKey, {
        rows: bucket.rows ?? EMPTY_ARRAY,
        total: bucket.count,
        fetching: groups.fetching,
        error: bucket.rows === undefined
          ? new Error("The groups read carried no first page for this group.")
          : null,
        updatedAt: groups.updatedAt,
        refetch: groups.refetch,
      });
    } else {
      leafScopes.push({
        key: bucketKey,
        filter: cumulativeFilter,
        order: leafOrder,
        page: currentPage,
        pageSize: leafPageSize,
      });
    }
    const leaf = carriedLeaves.get(bucketKey) ?? leafResults.get(bucketKey);
    const rows = rowModelsByScopeKey.get(bucketKey) ?? EMPTY_ARRAY;
    const place: SelectionPlace = { path: groupPath, page: stableSerialize([bucketKey, currentPage, leafPageSize]) };
    // A record page lists its rows once its own read has settled; a carried page settles with its groups.
    pages.set(place.page, leaf && !leaf.error && leaf.updatedAt !== null ? new Set(rows.map((row) => row.id)) : null);
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
        message: errorFromUnknown(leaf.error)?.message ?? "Request failed.",
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
        items.push({
          kind: "record",
          itemKey: `${bucketKey}:${row.id}`,
          row,
          nav,
          place,
        });
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
    parentPath: SelectionGroupPath,
  ): GroupedListPager | undefined => {
    const axisGroup = groupStack[depth];
    if (!axisGroup) return;
    if (!resourceQuery)
      throw new Error("Resource metadata is required for server grouping.");
    const axis = resourceQuery.group(axisGroup);
    const levelScopeKey = stableSerialize({
      axis: axis.spec,
      filter: parentFilter ?? null,
    });
    const pagination = paginationByScope[levelScopeKey];
    const levelPageSize =
      depth === 0 ? pageSize : pagination?.pageSize ?? pageSize;
    const storedPage =
      depth === 0 ? rootPage : (pagination?.pageIndex ?? 0) + 1;
    const isLeafLevel = depth === groupStack.length - 1;
    const query: GroupByRequestOptions = {
      ...resourceQuery.toGroupRead(axis, parentFilter),
      measures: queryMeasures,
      page: storedPage,
      pageSize: levelPageSize,
      ...(isLeafLevel
        ? { rows: { limit: declaredLeafPageSize, orderBy: resourceQuery.toOrderBy(leafOrder) } }
        : {}),
    };
    groupScopes.push({
      key: levelScopeKey,
      query,
      group: axisGroup,
      filter: parentFilter,
      order: leafOrder,
      page: storedPage,
      pageSize: levelPageSize,
      ...(isLeafLevel ? { leafPageSize: declaredLeafPageSize } : {}),
    });
    const result = groupByResults.get(levelScopeKey);
    if (depth === 0) rootResult = result;
    const levelPage = stableSerialize([levelScopeKey, storedPage, levelPageSize]);
    const answered = params.requested?.get(levelScopeKey);
    const answers = !params.requested || (answered !== undefined && stableSerialize(answered) === stableSerialize(query));
    const listed = answers && result && !result.error && result.updatedAt !== null ? new Set<string>() : null;
    pages.set(levelPage, listed);
    const pager: GroupedListPager = {
      pageKey: levelScopeKey,
      page:
        result && !result.error
          ? Math.min(
              storedPage,
              Math.max(1, Math.ceil(result.totalCount / levelPageSize)),
            )
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
            message:
              errorFromUnknown(result.error)?.message ?? "Request failed.",
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

    for (const [index, bucket] of result.buckets.entries()) {
      const bucketFilter = axis.drill(bucket);
      const expandable = bucketFilter !== undefined;
      const bucketKey = stableSerialize({
        scope: levelScopeKey,
        bucket: bucket.key ?? null,
      });
      const position = (storedPage - 1) * levelPageSize + index;
      if (listed) {
        listed.add(bucketKey);
        positions.set(bucketKey, position);
      }
      const expanded = expandable && expandedKeys.has(bucketKey);
      const label = bucketLabel(
        bucket,
        axisGroup,
        modelMetadata,
        allRecordsLabel,
        t,
        emptyRelationLabel,
        resourceQuery,
      );
      const header: Extract<GroupedListItem<TRow>, { kind: "groupHeader" }> = {
        kind: "groupHeader",
        bucketKey,
        depth,
        label,
        count: bucket.count,
        expandable,
        ...(!expandable && axis.extraction && !axis.extraction.drill
          ? { unavailableMessage: t("search.numberPartItemsUnavailable") } : {}),
        expanded,
        bucket,
      };
      items.push(header);
      if (!expanded || bucketFilter === undefined) continue;
      const cumulativeFilter = Filter.combine(parentFilter ?? {}, bucketFilter);
      const identity = axis.bucketIdentity(bucket);
      const groupPath: SelectionGroupPath = [...parentPath, {
        group: axis.spec,
        key: identity === null ? null : String(identity),
        label,
        position,
        bucketKey,
        page: levelPage,
      }];
      header.pager = isLeafLevel
        ? emitLeaf(bucketKey, cumulativeFilter, bucket, depth, result, groupPath)
        : walkLevel(depth + 1, cumulativeFilter, groupPath);
    }
    return pager;
  };

  walkLevel(0, baseFilter, []);
  return { groupScopes, leafScopes, leafKeys, carriedLeaves, items, rootResult, shown: { pages, positions } };
}

function bucketLabel(
  bucket: AggregateBucket,
  group: ResourceViewGroup | undefined,
  metadata: ModelMetadata | null,
  allRecordsLabel: string,
  t: UiTranslate,
  emptyRelationLabel: (field: string) => string,
  query: ResourceQuery,
): string {
  if (!group) return allRecordsLabel;
  const [label] = bucketValueLabels(
    bucket,
    [group],
    metadata,
    t,
    emptyRelationLabel,
    query,
  );
  return label ?? allRecordsLabel;
}

/**
 * Where the render shows each loaded record: its place, or null when it shows the
 * record under more than one group path (a stale page of one group beside a fresh
 * page of another), which places it nowhere.
 */
export function placedRecords<TRow extends Row>(
  items: readonly GroupedListItem<TRow>[],
): ReadonlyMap<string, SelectionPlace | null> {
  const placed = new Map<string, SelectionPlace | null>();
  for (const item of items) {
    if (item.kind !== "record") continue;
    const seen = placed.get(item.row.id);
    placed.set(item.row.id, seen === undefined || (seen !== null && groupPathId(seen.path) === groupPathId(item.place.path))
      ? item.place : null);
  }
  return placed;
}

/** Whether a page the render shows still lists a member; a page it does not show cannot tell. */
function stillListed(shown: GroupedRenderShown, page: string, member: string): boolean {
  const listed = shown.pages.get(page);
  return listed === undefined || (listed !== null && listed.has(member));
}

/**
 * Split the selected ids by `grouping`, the levels the surface renders: from the
 * places captured with them, checked against the render's items and the pages it
 * shows now; null when the surface places no rows. See `ListViewProps.bulkActions`
 * for the three states.
 */
export function selectedGroupsOf<TRow extends Row>(
  selectedIds: Iterable<string>,
  grouping: readonly ResourceViewGroup[],
  render: {
    places: Readonly<Record<string, SelectionPlace>>;
    items: readonly GroupedListItem<TRow>[];
    shown: GroupedRenderShown;
  } | null,
): readonly SelectedGroup[] | null | undefined {
  if (grouping.length === 0) return undefined;
  if (render === null) return null;
  const placed = placedRecords(render.items);
  // Whole level specs in order: field, granularity and `repeated`.
  const levels = stableSerialize(grouping);
  const entries = new Map<string, { path: SelectionGroupPath; ids: string[] }>();
  for (const id of selectedIds) {
    const captured = render.places[id];
    const current = placed.get(id);
    // Never a partial or stale split. A row has no place when it was selected without
    // one or under other levels; when it shows now under another group or several; or
    // when a page that listed it or one of its groups shows again settled without it,
    // not yet settled or failed (a page that refetches keeps what it last settled with).
    if (!captured || current === null || stableSerialize(captured.path.map(({ group }) => group)) !== levels
      || !stillListed(render.shown, captured.page, id)
      || captured.path.some(({ page, bucketKey }) => !stillListed(render.shown, page, bucketKey))) return null;
    const identity = groupPathId(captured.path);
    if (current !== undefined && groupPathId(current.path) !== identity) return null;
    const entry = entries.get(identity);
    if (!entry) entries.set(identity, { path: current?.path ?? captured.path, ids: [id] });
    else {
      entry.ids.push(id);
      // A loaded row carries the group's current label.
      if (current) entry.path = current.path;
    }
  }
  // One rank per group, so the order is consistent under nested grouping: its
  // position in this render where a settled page lists it, else the position the
  // first selected entry captured; then selection-map order, so ties keep it and
  // siblings stay together under their parent.
  const ranked = [...entries.values()].map((entry) => ({
    ...entry, groups: entry.path.map((_, depth) => groupPathId(entry.path.slice(0, depth + 1))),
  }));
  const ranks = new Map<string, readonly [number, number]>();
  for (const { path, groups } of ranked) groups.forEach((group, depth) => {
    if (!ranks.has(group)) ranks.set(group, [render.shown.positions.get(path[depth]!.bucketKey) ?? path[depth]!.position, ranks.size]);
  });
  return ranked
    .sort((left, right) => {
      const depth = left.groups.findIndex((group, index) => group !== right.groups[index]);
      const [leftRank, rightRank] = [ranks.get(left.groups[depth]!)!, ranks.get(right.groups[depth]!)!];
      return leftRank[0] - rightRank[0] || leftRank[1] - rightRank[1];
    })
    .map(({ path, ids }) => {
      const levels = path.map(({ group, key, label }) => ({ field: group.field, key, label }));
      return { ...levels.at(-1)!, parents: levels.slice(0, -1), ids };
    });
}

/** A path's identity: each level's axis spec and group key. */
export function groupPathId(path: SelectionGroupPath): string {
  return stableSerialize(path.map(({ group, key }) => [group, key]));
}

export function groupScopesEqual(
  left: readonly GroupByBatchScope[],
  right: readonly GroupByBatchScope[],
): boolean {
  if (left === right) return true;
  if (left.length !== right.length) return false;
  return left.every((scope, index) => {
    const other = right[index];
    return (
      other !== undefined &&
      scope.key === other.key &&
      stableSerialize(scope) === stableSerialize(other)
    );
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

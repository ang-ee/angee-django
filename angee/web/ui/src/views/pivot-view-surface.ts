// The pivot surface: the one owner of a cross-tabulated resource view's data
// model. It reads the same server grouping every other grouped surface reads —
// `<resource>_groups(group_by: [...])` batched through `useAngeeGroupByBatch`,
// plus `<resource>_aggregate` for the grand total — and turns it into the row
// axis tree, the column axis tree, the measured cells between them, and the
// subtotals down both edges.
//
// The request shape is the point: one grouped call per axis level, one per
// (row level × column level) cell block, and one aggregate. That is
// (rows+1) × (columns+1) calls set by axis depth alone — never by cell count —
// and every subtotal is computed database-side, so a non-additive measure
// (`avg`) stays exact instead of being re-derived from cell values. Axis levels
// load lazily: a level is requested only once its parent level resolved with an
// expanded member, and each cell block is scoped by `where` to the members its
// two levels loaded, which is what keeps a paged, wide pivot sparse.
import * as React from "react";
import type { ModelMetadata, Row } from "@angee/metadata";
import {
  crudFiltersFromFilterRecord,
  hasuraWhereFromCrudFilters,
  useAngeeAggregate,
  useAngeeGroupByBatch,
  type AggregateBucket,
  type GroupByBatchScope,
  type GroupByRequestOptions,
  type GroupDimension as HasuraGroupDimension,
  type GroupOrder,
  type UseAngeeGroupByResult,
} from "@angee/refine";

import { errorFromUnknown } from "../data/errors";
import { useUiT } from "../i18n";
import type { ResourceViewContextValue } from "./resource-view-context";
import {
  Filter,
  stableSerialize,
  type ResourceViewFilter,
  type ResourceViewGroup,
  type ResourceViewSort,
} from "./resource-view-model";
import {
  bucketFilterForGroup,
  bucketValueLabels,
  groupFieldLabel,
  groupLabelDimension,
  groupMeasuresFromColumns,
  hasuraGroupDimension,
  hasuraGroupOrderForDimensions,
  hasuraMeasuresFromGroupMeasures,
  resourceViewGroupToAggregateDimension,
  type GroupByDimension,
  type GroupMeasure,
} from "./resource-view-list-body";
import type { ColumnDescriptor } from "./page";
import { useAggregateOperation, useGroupOperation } from "./resource-operations";
import type { PivotViewSpec } from "./resource-view-types";
import { validResourceViewGroupStack } from "./resource-view-utils";

/** Members loaded per nested axis level; the outermost row axis uses the pager. */
const PIVOT_AXIS_LEVEL_LIMIT = 50;

const EMPTY_SCOPES: readonly GroupByBatchScope[] = [];
const EMPTY_NODES: readonly PivotAxisNode[] = [];
const EMPTY_TOGGLED_KEYS: ReadonlySet<string> = new Set();
const EMPTY_AXIS: readonly ResourceViewGroup[] = [];
const EMPTY_MEASURE_IDS: readonly string[] = [];

/** Which edge an axis runs along; the two edges share every mechanic below. */
export type PivotAxisSide = "row" | "column";

/** One loaded member of a pivot axis — a bucket plus its place in the axis tree. */
export interface PivotAxisNode {
  /** Stable path identity: the axis key values from the outermost level down. */
  key: string;
  side: PivotAxisSide;
  depth: number;
  /** This member's own label (its innermost axis level's bucket label). */
  label: string;
  /** Cumulative record filter for this member, the view's base filter excluded. */
  filter: ResourceViewFilter;
  /** The member's subtotal across the opposite axis — a server-side aggregate. */
  bucket: AggregateBucket;
  expandable: boolean;
  expanded: boolean;
  children: readonly PivotAxisNode[];
}

/** One rendered line of the row edge, in display order. */
export interface PivotRowItem {
  node: PivotAxisNode;
  /** Nesting depth, for the label column's indent. */
  depth: number;
}

/** A capped axis level, reported so the view can say what it left out. */
export interface PivotAxisTruncation {
  side: PivotAxisSide;
  depth: number;
  loaded: number;
  total: number;
}

export interface UsePivotResourceViewSurfaceProps<TRow extends Row = Row> {
  resource: string;
  columns: readonly ColumnDescriptor<TRow>[];
  pivot: PivotViewSpec;
  /** The page's own filter, merged under the view's URL-owned filter. */
  filter?: Record<string, unknown>;
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
}

export interface PivotResourceViewSurface {
  /** Row axes in effect: the URL-owned group stack, else the declaration. */
  rowStack: readonly ResourceViewGroup[];
  /** Column axes in effect: the URL-owned column stack, else the declaration. */
  columnStack: readonly ResourceViewGroup[];
  /** The outermost row axis's display label, for the table's corner header. */
  rowAxisLabel: string;
  /** Measures in effect, in declaration order. */
  measures: readonly GroupMeasure[];
  /** Every measure the columns declare, for the measure picker. */
  availableMeasures: readonly GroupMeasure[];
  rowItems: readonly PivotRowItem[];
  columnNodes: readonly PivotAxisNode[];
  /** The innermost visible column members — one cell block per measure each. */
  columnLeaves: readonly PivotAxisNode[];
  /** The measured cell at (row member, column member); absent when empty. */
  cellAt: (rowKey: string, columnKey: string) => AggregateBucket | undefined;
  /** Grand total over the filtered scope. */
  grandTotal: AggregateBucket | null;
  toggleRow: (key: string) => void;
  toggleColumn: (key: string) => void;
  /** Swap the row and column axes, keeping measures and filter. */
  swapAxes: () => void;
  /** The record filter a cell drills down into; `undefined` when not drillable. */
  drilldownFilter: (
    rowNode: PivotAxisNode | null,
    columnNode: PivotAxisNode | null,
  ) => ResourceViewFilter | undefined;
  /** Drill a cell down by filtering the resource view to it and opening the list. */
  drillDown: (
    rowNode: PivotAxisNode | null,
    columnNode: PivotAxisNode | null,
  ) => void;
  /** Axis levels whose members were capped, so the view can report them. */
  truncations: readonly PivotAxisTruncation[];
  /** Exact member count of the outermost row axis, for the toolbar pager. */
  rowTotal: number | undefined;
  fetching: boolean;
  error: Error | null;
  refetch: () => void;
}

export function usePivotResourceViewSurface<TRow extends Row = Row>({
  resource,
  columns,
  pivot,
  filter,
  resourceView,
  modelMetadata = null,
}: UsePivotResourceViewSurfaceProps<TRow>): PivotResourceViewSurface {
  const t = useUiT();
  const dataResource = requirePivotDataResource(resource, modelMetadata);
  const groupOperation = useGroupOperation(dataResource);
  const aggregateOperation = useAggregateOperation(dataResource);

  // Both declarations and URL state cross the metadata boundary here. Resolve
  // relation aliases and discard unsupported axes before any grouped document is
  // constructed; malformed deep links must not turn into render-time exceptions.
  const declaredRowStack = React.useMemo(
    () => validResourceViewGroupStack(pivot.rows, modelMetadata),
    [modelMetadata, pivot.rows],
  );
  const declaredColumnStack = React.useMemo(
    () => validResourceViewGroupStack(pivot.columns ?? EMPTY_AXIS, modelMetadata),
    [modelMetadata, pivot.columns],
  );
  const currentRowStack = React.useMemo(
    () => validResourceViewGroupStack(resourceView.state.groupStack, modelMetadata),
    [modelMetadata, resourceView.state.groupStack],
  );
  const currentColumnStack = React.useMemo(
    () => validResourceViewGroupStack(resourceView.state.columnStack, modelMetadata),
    [modelMetadata, resourceView.state.columnStack],
  );
  const rowStack = currentRowStack.length > 0
    ? currentRowStack
    : declaredRowStack;
  const columnStack = resourceView.state.hasColumnStack
    ? currentColumnStack
    : declaredColumnStack;

  const availableMeasures = React.useMemo(
    () => groupMeasuresFromColumns(columns),
    [columns],
  );
  const selectedMeasureIds = resourceView.state.measures.length > 0
    ? resourceView.state.measures
    : (pivot.measures ?? EMPTY_MEASURE_IDS);
  const measures = React.useMemo(
    () => selectedMeasures(availableMeasures, selectedMeasureIds),
    [availableMeasures, selectedMeasureIds],
  );
  const queryMeasures = React.useMemo(
    () => hasuraMeasuresFromGroupMeasures(measures, modelMetadata),
    [measures, modelMetadata],
  );

  const mergedFilter = React.useMemo(
    () => Filter.combineOptional(filter, resourceView.state.filter),
    [filter, resourceView.state.filter],
  );

  // Expansion is short-lived interaction state, so it stays local (the grouped
  // list makes the same call). A key in these sets means "toggled away from the
  // declared default", which is what lets a declared auto-expanded level collapse.
  const [toggledRowKeys, setToggledRowKeys] =
    React.useState<ReadonlySet<string>>(EMPTY_TOGGLED_KEYS);
  const [toggledColumnKeys, setToggledColumnKeys] =
    React.useState<ReadonlySet<string>>(EMPTY_TOGGLED_KEYS);
  const toggleRow = React.useCallback((key: string) => {
    setToggledRowKeys((current) => toggledKeys(current, key));
  }, []);
  const toggleColumn = React.useCallback((key: string) => {
    setToggledColumnKeys((current) => toggledKeys(current, key));
  }, []);

  const params = React.useMemo<PivotModelParams>(
    () => ({
      rowStack,
      columnStack,
      measures: queryMeasures,
      baseFilter: mergedFilter,
      toggledRowKeys,
      toggledColumnKeys,
      defaultRowDepth: pivot.expandRows ?? 0,
      defaultColumnDepth: pivot.expandColumns ?? 0,
      page: resourceView.state.page,
      pageSize: resourceView.state.pageSize,
      sort: resourceView.state.sort,
      modelMetadata,
      emptyValueLabel: t("list.emptyValue"),
      emptyRelationLabel: (field: string) =>
        t("list.emptyRelation", {
          relation: (
            modelMetadata?.fields[field]?.label ?? groupFieldLabel(field)
          ).toLocaleLowerCase(),
        }),
    }),
    [
      rowStack,
      columnStack,
      queryMeasures,
      mergedFilter,
      toggledRowKeys,
      toggledColumnKeys,
      pivot.expandRows,
      pivot.expandColumns,
      resourceView.state.page,
      resourceView.state.pageSize,
      resourceView.state.sort,
      modelMetadata,
      t,
    ],
  );

  // The desired scope set grows as levels resolve (a nested level is knowable
  // only once its parent's members arrived), so it stages through state exactly
  // like the grouped list surface stages its per-level scopes.
  const [scopes, setScopes] =
    React.useState<readonly GroupByBatchScope[]>(EMPTY_SCOPES);
  const groupResults = useAngeeGroupByBatch(groupOperation.target, scopes, {
    document: groupOperation.document,
    enabled: rowStack.length > 0,
  });
  const model = React.useMemo(
    () => buildPivotModel(groupResults, params),
    [groupResults, params],
  );
  const desiredScopes = model.scopes;
  React.useEffect(() => {
    setScopes((current) =>
      scopesEqual(current, desiredScopes) ? current : desiredScopes,
    );
  }, [desiredScopes]);

  const where = React.useMemo(
    () => hasuraWhereFromCrudFilters(crudFiltersFromFilterRecord(mergedFilter)),
    [mergedFilter],
  );
  const grandTotal = useAngeeAggregate(aggregateOperation.target, {
    document: aggregateOperation.document,
    where,
    measures: queryMeasures,
    enabled: rowStack.length > 0,
  });

  const swapAxes = React.useCallback(() => {
    resourceView.setGroupStack(columnStack);
    resourceView.setColumnStack(rowStack);
  }, [columnStack, resourceView, rowStack]);

  const drilldownFilter = React.useCallback(
    (rowNode: PivotAxisNode | null, columnNode: PivotAxisNode | null) => {
      if (pivot.drilldown === "none") return undefined;
      const cellFilter = Filter.combine(
        rowNode?.filter ?? {},
        columnNode?.filter ?? {},
      );
      return Object.keys(cellFilter).length > 0 ? cellFilter : undefined;
    },
    [pivot.drilldown],
  );
  const drillDown = React.useCallback(
    (rowNode: PivotAxisNode | null, columnNode: PivotAxisNode | null) => {
      const cellFilter = drilldownFilter(rowNode, columnNode);
      if (!cellFilter) return;
      // The cell's own axis filters become the view filter and the list kind
      // renders them: the drilldown is the same URL-owned filter every other
      // surface reads, not a private pivot navigation.
      resourceView.setFilter(Filter.combine(resourceView.state.filter, cellFilter));
      resourceView.setView("list");
    },
    [drilldownFilter, resourceView],
  );

  const results = React.useMemo(() => [...groupResults.values()], [groupResults]);
  const refetch = React.useCallback(() => {
    grandTotal.refetch();
    results.forEach((entry) => entry.refetch());
  }, [grandTotal, results]);

  return {
    rowStack,
    columnStack,
    rowAxisLabel: axisFieldLabel(rowStack[0], modelMetadata, t("list.allRecords")),
    measures,
    availableMeasures,
    rowItems: model.rowItems,
    columnNodes: model.columnNodes,
    columnLeaves: model.columnLeaves,
    cellAt: model.cellAt,
    grandTotal: grandTotal.aggregate,
    toggleRow,
    toggleColumn,
    swapAxes,
    drilldownFilter,
    drillDown,
    truncations: model.truncations,
    rowTotal: model.rowTotal,
    fetching: grandTotal.fetching || results.some((entry) => entry.fetching),
    error:
      errorFromUnknown(results.find((entry) => entry.error)?.error)
      ?? errorFromUnknown(grandTotal.error),
    refetch,
  };
}

interface PivotModelParams {
  rowStack: readonly ResourceViewGroup[];
  columnStack: readonly ResourceViewGroup[];
  measures: readonly GroupMeasure[];
  baseFilter: ResourceViewFilter | undefined;
  toggledRowKeys: ReadonlySet<string>;
  toggledColumnKeys: ReadonlySet<string>;
  /** Row axis levels expanded unless toggled shut. */
  defaultRowDepth: number;
  /** Column axis levels expanded unless toggled shut. */
  defaultColumnDepth: number;
  page: number;
  pageSize: number;
  sort: ResourceViewSort | null;
  modelMetadata: ModelMetadata | null;
  emptyValueLabel: string;
  emptyRelationLabel: (field: string) => string;
}

/** One axis member before the tree is assembled — it still names its parent. */
interface PivotAxisMember {
  node: PivotAxisNode;
  parentKey: string | null;
}

interface PivotAxisLevel {
  depth: number;
  /** The filter every member of this level was queried under. */
  where: ResourceViewFilter | undefined;
  /** Whether this level is a paged window (the outermost row axis) or whole. */
  windowed: boolean;
  members: readonly PivotAxisMember[];
  /** Exact member count before the level's window. */
  total: number;
}

interface PivotModel {
  scopes: GroupByBatchScope[];
  rowItems: readonly PivotRowItem[];
  columnNodes: readonly PivotAxisNode[];
  columnLeaves: readonly PivotAxisNode[];
  cellAt: (rowKey: string, columnKey: string) => AggregateBucket | undefined;
  truncations: readonly PivotAxisTruncation[];
  rowTotal: number | undefined;
}

/**
 * Walk both axes and the cell blocks between them once, collecting the grouped
 * scopes to fetch and the render model the loaded ones already support. Pure:
 * the same call yields the next scope frontier and the model, so the surface
 * can run it before and after the batch resolves.
 */
function buildPivotModel(
  groupResults: ReadonlyMap<string, UseAngeeGroupByResult>,
  params: PivotModelParams,
): PivotModel {
  const scopes: GroupByBatchScope[] = [];
  const truncations: PivotAxisTruncation[] = [];
  const rows = buildAxis("row", params.rowStack, groupResults, params, scopes, truncations);
  const columns = buildAxis(
    "column",
    params.columnStack,
    groupResults,
    params,
    scopes,
    truncations,
  );
  const rowNodes = assembleAxisTree(rows);
  const columnNodes = assembleAxisTree(columns);
  const cells = buildCells(rows, columns, groupResults, params, scopes);
  return {
    scopes,
    rowItems: axisRowItems(rowNodes),
    columnNodes,
    columnLeaves: visibleAxisLeaves(columnNodes),
    cellAt: (rowKey, columnKey) => cells.get(cellKey(rowKey, columnKey)),
    truncations,
    rowTotal: rows[0]?.total,
  };
}

/**
 * Load one axis level at a time, lazily: level 0 is the paged window of the
 * outermost axis, and level `d` is queried under the union of level `d - 1`'s
 * expanded members. The walk stops at the first unresolved level, which is what
 * makes the collected scopes the frontier to fetch next.
 */
function buildAxis(
  side: PivotAxisSide,
  stack: readonly ResourceViewGroup[],
  groupResults: ReadonlyMap<string, UseAngeeGroupByResult>,
  params: PivotModelParams,
  scopes: GroupByBatchScope[],
  truncations: PivotAxisTruncation[],
): PivotAxisLevel[] {
  const levels: PivotAxisLevel[] = [];
  for (let depth = 0; depth < stack.length; depth += 1) {
    const parents = depth === 0 ? null : levels[depth - 1]?.members ?? [];
    const expandedParents = parents?.filter((member) => member.node.expanded) ?? null;
    if (expandedParents !== null && expandedParents.length === 0) break;
    const where = expandedParents
      ? Filter.combineOptional(
          params.baseFilter,
          anyOfFilter(expandedParents.map((member) => member.node.filter)),
        )
      : params.baseFilter;
    const windowed = depth === 0 && side === "row";
    const query = axisQuery(side, stack, depth, where, windowed, params);
    const key = axisScopeKey(side, depth, query);
    scopes.push({ key, query });
    const result = groupResults.get(key);
    if (!result || result.error) break;
    const members = result.buckets.map((bucket) =>
      axisMember(bucket, side, depth, stack, parents, params),
    );
    levels.push({ depth, where, windowed, members, total: result.totalCount });
    if (result.totalCount > members.length) {
      truncations.push({
        side,
        depth,
        loaded: members.length,
        total: result.totalCount,
      });
    }
    if (members.length === 0) break;
  }
  return levels;
}

function axisQuery(
  side: PivotAxisSide,
  stack: readonly ResourceViewGroup[],
  depth: number,
  where: ResourceViewFilter | undefined,
  windowed: boolean,
  params: PivotModelParams,
): GroupByRequestOptions {
  const dimensions = axisDimensions(stack, depth, params.modelMetadata);
  const orderBy = axisOrder(side, dimensions, params);
  const hasuraWhere = hasuraWhereFromCrudFilters(crudFiltersFromFilterRecord(where));
  return {
    dimensions,
    ...(orderBy ? { orderBy } : {}),
    ...(hasuraWhere !== undefined ? { where: hasuraWhere } : {}),
    measures: params.measures,
    page: windowed ? params.page : 1,
    pageSize: windowed ? params.pageSize : PIVOT_AXIS_LEVEL_LIMIT,
  };
}

/**
 * Order the row axis by the sorted measure when the view sorts on one, else by
 * the axis key — the grouped list's default. A measure sort names the aggregate
 * alias the grouping owner exposes (`count`, `<op>_<field>`). The column edge
 * always reads in axis order: sorting rows by a measure must not reshuffle the
 * headers the reader is comparing across.
 */
function axisOrder(
  side: PivotAxisSide,
  dimensions: readonly HasuraGroupDimension[],
  params: PivotModelParams,
): readonly GroupOrder[] | undefined {
  const measure = side === "row" ? sortedMeasure(params) : undefined;
  if (measure) {
    return [
      {
        field: measureOrderAlias(measure),
        direction: params.sort?.dir === "desc" ? "DESC" : "ASC",
      },
    ];
  }
  return hasuraGroupOrderForDimensions(dimensions);
}

function sortedMeasure(params: PivotModelParams): GroupMeasure | undefined {
  const field = params.sort?.field;
  if (!field) return undefined;
  return params.measures.find((measure) => measure.columnId === field);
}

/** The `order_by` alias for one measure, as the grouping owner names it. */
export function measureOrderAlias(measure: GroupMeasure): string {
  return measure.op === "count" ? "count" : `${measure.op}_${measure.field}`;
}

function axisDimensions(
  stack: readonly ResourceViewGroup[],
  depth: number,
  metadata: ModelMetadata | null,
): HasuraGroupDimension[] {
  const dimensions: GroupByDimension[] = [];
  for (const group of stack.slice(0, depth + 1)) {
    dimensions.push(resourceViewGroupToAggregateDimension(group, metadata));
    const labelDimension = groupLabelDimension(group, metadata);
    if (labelDimension) dimensions.push(labelDimension);
  }
  return dimensions.map(hasuraGroupDimension);
}

function axisMember(
  bucket: AggregateBucket,
  side: PivotAxisSide,
  depth: number,
  stack: readonly ResourceViewGroup[],
  parents: readonly PivotAxisMember[] | null,
  params: PivotModelParams,
): PivotAxisMember {
  const group = stack[depth]!;
  const key = axisPathKey(side, bucket, stack, depth, params.modelMetadata);
  const parentKey =
    depth === 0
      ? null
      : axisPathKey(side, bucket, stack, depth - 1, params.modelMetadata);
  const parentFilter =
    parents?.find((member) => member.node.key === parentKey)?.node.filter ?? {};
  const ownFilter = bucketFilterForGroup(bucket, group, params.modelMetadata);
  const labels = bucketValueLabels(
    bucket,
    stack.slice(0, depth + 1),
    params.modelMetadata,
    params.emptyValueLabel,
    params.emptyRelationLabel,
  );
  const expandable = depth < stack.length - 1 && ownFilter !== undefined;
  const toggledKeySet =
    side === "row" ? params.toggledRowKeys : params.toggledColumnKeys;
  const defaultDepth =
    side === "row" ? params.defaultRowDepth : params.defaultColumnDepth;
  const expandedByDefault = depth < defaultDepth;
  return {
    parentKey,
    node: {
      key,
      side,
      depth,
      label: labels[labels.length - 1] ?? params.emptyValueLabel,
      // Cumulative from the outermost level down, so the member's filter selects
      // exactly the records it measures — and scopes the level below it.
      filter: Filter.combine(parentFilter, ownFilter ?? {}),
      bucket,
      expandable,
      expanded:
        expandable
        && (expandedByDefault ? !toggledKeySet.has(key) : toggledKeySet.has(key)),
      children: EMPTY_NODES,
    },
  };
}

/** Assemble the loaded levels into a tree, linking each member under its parent. */
function assembleAxisTree(levels: readonly PivotAxisLevel[]): readonly PivotAxisNode[] {
  const membersByParent = new Map<string, PivotAxisMember[]>();
  const roots: PivotAxisMember[] = [];
  for (const level of levels) {
    for (const member of level.members) {
      if (member.parentKey === null) {
        roots.push(member);
        continue;
      }
      const siblings = membersByParent.get(member.parentKey) ?? [];
      siblings.push(member);
      membersByParent.set(member.parentKey, siblings);
    }
  }
  const build = (member: PivotAxisMember): PivotAxisNode => ({
    ...member.node,
    children: member.node.expanded
      ? (membersByParent.get(member.node.key) ?? []).map(build)
      : EMPTY_NODES,
  });
  return roots.map(build);
}

/**
 * The stable identity of an axis member: its axis key values from the outermost
 * level down to `depth`. Cell rows carry the same axis key values, so a cell
 * finds its row and column member without the surface re-decoding the bucket.
 */
function axisPathKey(
  side: PivotAxisSide,
  bucket: AggregateBucket,
  stack: readonly ResourceViewGroup[],
  depth: number,
  metadata: ModelMetadata | null,
): string {
  const values = stack.slice(0, depth + 1).map((group) => {
    const dimension = resourceViewGroupToAggregateDimension(group, metadata);
    return bucket.key?.[dimension.key ?? dimension.field] ?? null;
  });
  return `${side}:${stableSerialize(values)}`;
}

/**
 * One grouped call per (row level × column level): that block's cells group by
 * both axes' dimensions at once, scoped by `where` to the members those two
 * levels loaded, so a paged row window never pulls cells it cannot show.
 */
function buildCells(
  rows: readonly PivotAxisLevel[],
  columns: readonly PivotAxisLevel[],
  groupResults: ReadonlyMap<string, UseAngeeGroupByResult>,
  params: PivotModelParams,
  scopes: GroupByBatchScope[],
): Map<string, AggregateBucket> {
  const cells = new Map<string, AggregateBucket>();
  for (const rowLevel of rows) {
    for (const columnLevel of columns) {
      const query = cellQuery(rowLevel, columnLevel, params);
      const key = cellScopeKey(rowLevel.depth, columnLevel.depth, query);
      scopes.push({ key, query });
      const result = groupResults.get(key);
      if (!result || result.error) continue;
      for (const bucket of result.buckets) {
        const rowKey = axisPathKey(
          "row",
          bucket,
          params.rowStack,
          rowLevel.depth,
          params.modelMetadata,
        );
        const columnKey = axisPathKey(
          "column",
          bucket,
          params.columnStack,
          columnLevel.depth,
          params.modelMetadata,
        );
        cells.set(cellKey(rowKey, columnKey), bucket);
      }
    }
  }
  return cells;
}

function cellQuery(
  rowLevel: PivotAxisLevel,
  columnLevel: PivotAxisLevel,
  params: PivotModelParams,
): GroupByRequestOptions {
  const where = Filter.combineOptional(
    levelScopeFilter(rowLevel, params),
    anyOfFilter(columnLevel.members.map((member) => member.node.filter)),
  );
  const hasuraWhere = hasuraWhereFromCrudFilters(crudFiltersFromFilterRecord(where));
  return {
    dimensions: [
      ...axisDimensions(params.rowStack, rowLevel.depth, params.modelMetadata),
      ...axisDimensions(params.columnStack, columnLevel.depth, params.modelMetadata),
    ],
    ...(hasuraWhere !== undefined ? { where: hasuraWhere } : {}),
    measures: params.measures,
    // Deliberately unpaged: a block holds one cell per (row member × column
    // member), so the page-size clamp the axis windows ride would silently drop
    // cells out of the middle of the matrix. Its size is already bounded by the
    // two axis windows the `where` above echoes.
  };
}

/**
 * The scope a cell block inherits from its row level. A windowed level (the
 * paged outermost axis) must restrict the block to the members it loaded; a
 * nested level was already queried under its expanded parents, so its own
 * `where` is the scope.
 */
function levelScopeFilter(
  level: PivotAxisLevel,
  params: PivotModelParams,
): ResourceViewFilter | undefined {
  if (!level.windowed) return level.where;
  return Filter.combineOptional(
    params.baseFilter,
    anyOfFilter(level.members.map((member) => member.node.filter)),
  );
}

function cellKey(rowKey: string, columnKey: string): string {
  return `${rowKey}::${columnKey}`;
}

/** The `OR` of several member filters, or `undefined` when there is none to scope. */
function anyOfFilter(
  filters: readonly ResourceViewFilter[],
): ResourceViewFilter | undefined {
  const present = filters.filter((filter) => Object.keys(filter).length > 0);
  if (present.length === 0) return undefined;
  if (present.length === 1) return present[0];
  return { OR: present };
}

/** Flatten the row tree into display order: a member, then its expanded children. */
function axisRowItems(nodes: readonly PivotAxisNode[]): readonly PivotRowItem[] {
  const items: PivotRowItem[] = [];
  const walk = (node: PivotAxisNode): void => {
    items.push({ node, depth: node.depth });
    if (!node.expanded) return;
    for (const child of node.children) walk(child);
  };
  for (const node of nodes) walk(node);
  return items;
}

/** One header cell of the column edge, already spanned for its sub-tree. */
export interface PivotColumnHeaderCell {
  node: PivotAxisNode;
  colSpan: number;
  rowSpan: number;
}

/**
 * The column edge laid out as header rows, one per column axis level. A branch
 * spans its visible leaves; a leaf spans the levels below it. The axis owns this
 * because it is a fact of the loaded tree, not of the styling around it.
 */
export function pivotColumnHeaderRows(
  nodes: readonly PivotAxisNode[],
): readonly (readonly PivotColumnHeaderCell[])[] {
  const depthCount = axisDepthCount(nodes);
  if (depthCount === 0) return [];
  const rows: PivotColumnHeaderCell[][] = Array.from(
    { length: depthCount },
    () => [],
  );
  const walk = (node: PivotAxisNode): void => {
    const branch = node.expanded && node.children.length > 0;
    rows[node.depth]?.push({
      node,
      colSpan: axisLeafCount(node),
      rowSpan: branch ? 1 : depthCount - node.depth,
    });
    if (branch) for (const child of node.children) walk(child);
  };
  for (const node of nodes) walk(node);
  return rows;
}

function axisDepthCount(nodes: readonly PivotAxisNode[]): number {
  let depth = 0;
  const walk = (node: PivotAxisNode): void => {
    depth = Math.max(depth, node.depth + 1);
    if (node.expanded) for (const child of node.children) walk(child);
  };
  for (const node of nodes) walk(node);
  return depth;
}

function axisLeafCount(node: PivotAxisNode): number {
  if (!node.expanded || node.children.length === 0) return 1;
  return node.children.reduce((total, child) => total + axisLeafCount(child), 0);
}

/** The innermost visible member of each column branch — one cell column each. */
function visibleAxisLeaves(
  nodes: readonly PivotAxisNode[],
): readonly PivotAxisNode[] {
  const leaves: PivotAxisNode[] = [];
  const walk = (node: PivotAxisNode): void => {
    if (!node.expanded || node.children.length === 0) {
      leaves.push(node);
      return;
    }
    for (const child of node.children) walk(child);
  };
  for (const node of nodes) walk(node);
  return leaves;
}

/** An axis's display label: the model's own field label, else the path's title. */
function axisFieldLabel(
  group: ResourceViewGroup | undefined,
  metadata: ModelMetadata | null,
  fallback: string,
): string {
  if (!group) return fallback;
  const field = group.aggregateField ?? group.field;
  return metadata?.fields[field]?.label ?? groupFieldLabel(field);
}

function selectedMeasures(
  available: readonly GroupMeasure[],
  selected: readonly string[],
): readonly GroupMeasure[] {
  if (selected.length === 0) return available;
  const byId = new Map(available.map((measure) => [measure.columnId, measure]));
  const chosen = selected.flatMap((id) => {
    const measure = byId.get(id);
    return measure ? [measure] : [];
  });
  return chosen.length > 0 ? chosen : available;
}

function toggledKeys(
  keys: ReadonlySet<string>,
  key: string,
): ReadonlySet<string> {
  const next = new Set(keys);
  if (next.has(key)) next.delete(key);
  else next.add(key);
  return next;
}

function axisScopeKey(
  side: PivotAxisSide,
  depth: number,
  query: GroupByRequestOptions,
): string {
  return `${side}:${depth}:${stableSerialize(query)}`;
}

function cellScopeKey(
  rowDepth: number,
  columnDepth: number,
  query: GroupByRequestOptions,
): string {
  return `cell:${rowDepth}:${columnDepth}:${stableSerialize(query)}`;
}

function scopesEqual(
  left: readonly GroupByBatchScope[],
  right: readonly GroupByBatchScope[],
): boolean {
  if (left === right) return true;
  if (left.length !== right.length) return false;
  return left.every((scope, index) => scope.key === right[index]?.key);
}

function requirePivotDataResource(
  resourceId: string,
  metadata: ModelMetadata | null | undefined,
): NonNullable<ModelMetadata["resource"]> {
  const dataResource = metadata?.resource;
  if (!dataResource) {
    throw new Error(`Resource "${resourceId}" has no data resource metadata.`);
  }
  return dataResource;
}

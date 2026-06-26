// Server-driven grouped list body: folded group buckets recurse one axis at a
// time; only leaf buckets fetch records via the cumulative bucket filter echo.
// Imports the shared seam from ./ListInternals and Pager from ../ui/pager;
// must NOT import ListView (ListView depends on this module, not vice versa).
import * as React from "react";
import {
  rowPublicId,
  type Row,
} from "@angee/resources";
import {
  useList,
  type BaseRecord,
  type HttpError,
  } from "@refinedev/core";
import {
  getCoreRowModel,
  useReactTable,
  type Column as TableColumn,
  type ColumnDef,
  type Table as TableModel,
  type VisibilityState,
  } from "@tanstack/react-table";
import {
  crudFiltersFromFilterRecord,
  hasuraWhereFromCrudFilters,
  refineFieldsFromPaths,
  refineSortersFromAngeeOrder,
  type AggregateBucket,
  } from "@angee/refine";
import {
  useAngeeAggregate,
  useAngeeGroupBy,
} from "../data/hooks";
import type {
  ModelMetadata,
} from "@angee/resources";
import {
  refineResourceName,
} from "@angee/resources";
import { Glyph } from "../chrome/Glyph";
import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { titleCase } from "../lib/titleCase";
import { CountBadge } from "../ui/badge";
import { Pager } from "../ui/pager";
import { Skeleton } from "../ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";
import type { ResourceViewContextValue } from "./resource-view-context";
import {
  useResourceRowsSnapshot,
  type ResourceListSnapshot,
} from "./resource-view-surface";
import {
  Filter,
  stableSerialize,
  type ResourceViewGroup,
} from "./resource-view-model";
import {
  groupPagerStatesEqual,
  useExpandedKeys,
  type GroupPagerState,
} from "./grouped-list-utils";
import {
  ALIGN_CLASS,
  ListHeaderCell,
  ListEmpty,
  ListLoadingFooter,
  ListSkeletonRows,
  RecordRow,
  TABLE_SCROLL_STYLE,
  alignOf,
  bucketFilterForGroup,
  bucketValueLabels,
  groupLabelDimension,
  formatMeasure,
  groupMeasuresFromColumns,
  hasuraMeasuresFromGroupMeasures,
  hasuraGroupDimension,
  hasuraGroupOrderForDimensions,
  groupFieldLabel,
  measureValue,
  type GroupByDimension,
  type GroupMeasure,
  type VisibleFieldOption,
} from "./ListInternals";
import type { ColumnDescriptor } from "./page";
import type { ListEmptyContent } from "./list-view-types";

const GROUPED_LIST_ITEM_PAGE_SIZE = 20;

type ListFilter = Record<string, unknown>;
type ListOrder = Record<string, unknown>;
type RowRecord = BaseRecord & Row;

function formatPagerNumber(value: number): string {
  return value.toLocaleString();
}

export interface GroupedListBodyProps<TRow extends Row> {
  resource: string;
  columns: readonly ColumnDescriptor<TRow>[];
  table: TableModel<TRow>;
  tableColumns: readonly ColumnDef<TRow>[];
  columnVisibility: VisibilityState;
  visibleColumnCount: number;
  visibleFields?: readonly VisibleFieldOption[];
  onVisibleFieldToggle?: (id: string, visible: boolean) => void;
  resourceView: ResourceViewContextValue;
  groupStack: readonly ResourceViewGroup[];
  groupDimensions: readonly GroupByDimension[];
  requestedFields: readonly string[];
  mergedFilter: ListFilter | undefined;
  sortOrder: ListOrder | undefined;
  order: ListOrder | undefined;
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  emptyMessage: ListEmptyContent;
  modelMetadata?: ModelMetadata | null;
  onPagerStateChange: (state: GroupPagerState) => void;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

export function GroupedListBody<TRow extends Row>({
  resource,
  columns,
  table,
  tableColumns,
  columnVisibility,
  visibleColumnCount,
  visibleFields = [],
  onVisibleFieldToggle,
  resourceView,
  groupStack,
  groupDimensions,
  requestedFields,
  mergedFilter,
  sortOrder,
  order,
  interactive,
  rowHref,
  onRowClick,
  emptyMessage,
  modelMetadata = null,
  onPagerStateChange,
  onListStateChange,
}: GroupedListBodyProps<TRow>): React.ReactElement {
  const colSpan = Math.max(1, visibleColumnCount + 1);
  const measures = React.useMemo(
    () => groupMeasuresFromColumns(columns),
    [columns],
  );
  const queryMeasures = React.useMemo(
    () => hasuraMeasuresFromGroupMeasures(measures, modelMetadata),
    [measures, modelMetadata],
  );
  const dataResource = requireDataResource(resource, modelMetadata ?? null);
  const where = React.useMemo(
    () => hasuraWhereFromCrudFilters(crudFiltersFromFilterRecord(mergedFilter)),
    [mergedFilter],
  );
  const visibleColumns = table.getVisibleLeafColumns();
  const grandTotal = useAngeeAggregate(dataResource, {
    where,
    measures: queryMeasures,
    enabled: groupDimensions.length > 0 && measures.length > 0,
  });
  const [topPagerState, setTopPagerState] =
    React.useState<GroupPagerState | null>(null);
  const handlePagerStateChange = React.useCallback(
    (state: GroupPagerState) => {
      setTopPagerState((current) =>
        groupPagerStatesEqual(current, state) ? current : state,
      );
      onPagerStateChange(state);
    },
    [onPagerStateChange],
  );

  return (
    <>
      <div className="overflow-auto" style={TABLE_SCROLL_STYLE}>
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id}>
                {/* Grouped mode omits page-level select-all; per-row selection still works. */}
                <TableHead sticky className="w-8" />
                {group.headers.map((header, index) => (
                  <ListHeaderCell
                    key={header.id}
                    header={header}
                    resourceView={resourceView}
                    visibleFields={visibleFields}
                    onVisibleFieldToggle={onVisibleFieldToggle}
                    withVisibleFields={index === group.headers.length - 1}
                  />
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <GroupLevel
            resource={resource}
            measures={queryMeasures}
            axes={groupDimensions}
            groups={groupStack}
            filter={mergedFilter ?? {}}
            depth={0}
            page={resourceView.state.page}
            pageSize={resourceView.state.pageSize}
            enabled
            tableColumns={tableColumns}
            visibleColumns={visibleColumns}
            columnVisibility={columnVisibility}
            colSpan={colSpan}
            resourceView={resourceView}
            requestedFields={requestedFields}
            sortOrder={sortOrder}
            order={order}
            interactive={interactive}
            rowHref={rowHref}
            onRowClick={onRowClick}
            emptyMessage={emptyMessage}
            modelMetadata={modelMetadata}
            onPagerStateChange={handlePagerStateChange}
            onListStateChange={onListStateChange}
          />
          {measures.length > 0 ? (
            <GroupMeasureFooter
              table={table}
              measures={queryMeasures}
              aggregate={grandTotal.aggregate}
            />
          ) : null}
        </Table>
      </div>
      {topPagerState?.fetching ? (
        <ListLoadingFooter />
      ) : null}
    </>
  );
}

interface GroupRenderProps<TRow extends Row> {
  resource: string;
  tableColumns: readonly ColumnDef<TRow>[];
  visibleColumns: readonly TableColumn<TRow, unknown>[];
  columnVisibility: VisibilityState;
  colSpan: number;
  resourceView: ResourceViewContextValue;
  requestedFields: readonly string[];
  sortOrder: ListOrder | undefined;
  order: ListOrder | undefined;
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  modelMetadata?: ModelMetadata | null;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

interface GroupLevelProps<TRow extends Row> extends GroupRenderProps<TRow> {
  measures: readonly GroupMeasure[];
  axes: readonly GroupByDimension[];
  groups: readonly ResourceViewGroup[];
  filter: ListFilter;
  depth: number;
  page: number;
  pageSize: number;
  enabled: boolean;
  emptyMessage: ListEmptyContent;
  regionId?: string;
  onPagerStateChange?: (state: GroupPagerState) => void;
}

function GroupLevel<TRow extends Row>({
  resource,
  measures,
  axes,
  groups,
  filter,
  depth,
  page,
  pageSize,
  enabled,
  emptyMessage,
  regionId,
  onPagerStateChange,
  tableColumns,
  visibleColumns,
  columnVisibility,
  colSpan,
  resourceView,
  requestedFields,
  sortOrder,
  order,
  interactive,
  rowHref,
  onRowClick,
  modelMetadata = null,
  onListStateChange,
}: GroupLevelProps<TRow>): React.ReactElement | null {
  const t = useBaseT();
  const axis = axes[0];
  const currentGroup = groups[0];
  // Carry the relation's display label alongside the id (Odoo's
  // `(id, display_name)`): one extra group axis so the bucket key holds the name
  // for the header, while the id axis still owns the drill-down filter.
  const labelDimension = React.useMemo(
    () => (axis && currentGroup ? groupLabelDimension(currentGroup, modelMetadata) : null),
    [axis, currentGroup, modelMetadata],
  );
  const dimensions = React.useMemo(
    () => {
      if (!axis) return [];
      return labelDimension ? [axis, labelDimension] : [axis];
    },
    [axis, labelDimension],
  );
  const remainingAxes = React.useMemo(() => axes.slice(1), [axes]);
  const remainingGroups = React.useMemo(() => groups.slice(1), [groups]);
  const [localPage, setLocalPage] = React.useState(1);
  const levelPage = depth === 0 ? page : localPage;
  const levelEnabled = enabled && dimensions.length > 0;
  const dataResource = requireDataResource(resource, modelMetadata ?? null);
  const where = React.useMemo(
    () => hasuraWhereFromCrudFilters(crudFiltersFromFilterRecord(filter)),
    [filter],
  );
  const hasuraDimensions = React.useMemo(
    () => dimensions.map(hasuraGroupDimension),
    [dimensions],
  );
  const orderBy = React.useMemo(
    () => hasuraGroupOrderForDimensions(hasuraDimensions),
    [hasuraDimensions],
  );
  const groupAggregation = useAngeeGroupBy(dataResource, {
    dimensions: hasuraDimensions,
    orderBy,
    where,
    measures,
    page: levelPage,
    pageSize,
    enabled: levelEnabled,
  });
  const groupTotal = groupAggregation.totalCount ?? groupAggregation.buckets.length;

  React.useEffect(() => {
    if (depth !== 0 || !onPagerStateChange) return;
    onPagerStateChange({
      total: groupTotal,
      fetching: groupAggregation.fetching,
      error: errorFromUnknown(groupAggregation.error),
    });
  }, [
    depth,
    groupAggregation.error,
    groupAggregation.fetching,
    groupTotal,
    onPagerStateChange,
  ]);

  const scopeKey = React.useMemo(
    () => stableSerialize({ axis: axis ?? null, filter: filter ?? null, pageSize }),
    [axis, filter, pageSize],
  );
  React.useEffect(() => {
    if (depth === 0 || !enabled) return;
    setLocalPage(1);
  }, [depth, enabled, scopeKey]);
  React.useEffect(() => {
    if (depth === 0 || !enabled) return;
    const pageCount = Math.max(1, Math.ceil(groupTotal / pageSize));
    setLocalPage((current) => Math.min(current, pageCount));
  }, [depth, enabled, groupTotal, pageSize]);

  // expandedKeys (shared hook) and pageByKey are intentionally not pruned; old
  // entries restore state when groups reappear.
  const { expandedKeys, toggle: toggleExpanded } = useExpandedKeys();
  const [pageByKey, setPageByKey] = React.useState<Record<string, number>>({});
  const setGroupPage = React.useCallback((key: string, nextPage: number) => {
    setPageByKey((current) => ({
      ...current,
      [key]: normaliseLocalPage(nextPage),
    }));
  }, []);
  const setLevelPage = React.useCallback((nextPage: number) => {
    setLocalPage(normaliseLocalPage(nextPage));
  }, []);

  if (!levelEnabled) return null;
  if (groupAggregation.error) {
    return (
      <GroupLevelStatusBody
        id={regionId}
        colSpan={colSpan}
        depth={depth}
        className="py-6 text-danger-text"
      >
        {groupAggregation.error.message}
      </GroupLevelStatusBody>
    );
  }
  if (groupAggregation.buckets.length === 0 && groupAggregation.fetching) {
    return (
      <GroupLevelSkeletonBody
        id={regionId}
        colSpan={colSpan}
        depth={depth}
        loadingLabel={t("list.loading")}
      />
    );
  }
  if (groupAggregation.buckets.length === 0) {
    return (
      <GroupLevelStatusBody
        id={regionId}
        colSpan={colSpan}
        depth={depth}
        className="py-8 text-center text-fg-muted"
      >
        {depth === 0 ? <ListEmpty>{emptyMessage}</ListEmpty> : "No sub-groups."}
      </GroupLevelStatusBody>
    );
  }

  const pageCount = Math.max(
    1,
    Math.ceil(groupTotal / pageSize),
  );
  const currentPage = Math.min(levelPage, pageCount);

  return (
    <>
      {groupAggregation.buckets.map((bucket, index) => {
        const key = stableBucketKey(bucket);
        return (
          <GroupSection
            key={key}
            bodyId={index === 0 ? regionId : undefined}
            resource={resource}
            measures={measures}
            bucket={bucket}
            bucketKey={key}
            group={currentGroup}
            parentFilter={filter}
            depth={depth}
            remainingAxes={remainingAxes}
            remainingGroups={remainingGroups}
            pageSize={pageSize}
            tableColumns={tableColumns}
            visibleColumns={visibleColumns}
            columnVisibility={columnVisibility}
            colSpan={colSpan}
            resourceView={resourceView}
            requestedFields={requestedFields}
            sortOrder={sortOrder}
            order={order}
            interactive={interactive}
            rowHref={rowHref}
            onRowClick={onRowClick}
            emptyMessage={emptyMessage}
            modelMetadata={modelMetadata}
            expanded={expandedKeys.has(key)}
            page={pageByKey[key] ?? 1}
            onToggle={toggleExpanded}
            onPageChange={setGroupPage}
            onListStateChange={onListStateChange}
          />
        );
      })}
      {depth > 0 && groupTotal > 0 ? (
        <SubGroupPager
          label={currentGroup ? groupFieldLabel(currentGroup.field) : "Sub-group"}
          colSpan={colSpan}
          page={currentPage}
          pageSize={pageSize}
          total={groupTotal}
          onPageChange={setLevelPage}
        />
      ) : null}
    </>
  );
}

function GroupLevelStatusBody({
  id,
  colSpan,
  depth,
  className,
  children,
}: {
  id?: string;
  colSpan: number;
  depth: number;
  className: string;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <TableBody id={id}>
      <TableRow>
        <TableCell
          colSpan={colSpan}
          className={className}
          style={depthIndentStyle(depth)}
        >
          {children}
        </TableCell>
      </TableRow>
    </TableBody>
  );
}

function GroupLevelSkeletonBody({
  id,
  colSpan,
  depth,
  loadingLabel,
}: {
  id?: string;
  colSpan: number;
  depth: number;
  loadingLabel: React.ReactNode;
}): React.ReactElement {
  return (
    <TableBody id={id}>
      <TableRow>
        <TableCell
          aria-busy="true"
          aria-live="polite"
          className="sr-only"
          colSpan={colSpan}
          role="status"
        >
          {loadingLabel}
        </TableCell>
      </TableRow>
      {Array.from({ length: 4 }, (_, index) => (
        <TableRow key={index} aria-hidden="true">
          <TableCell
            colSpan={colSpan}
            className="bg-canvas py-2"
            style={depthIndentStyle(depth)}
          >
            <div className="flex h-8 items-center justify-between gap-3">
              <span className="flex min-w-0 items-center gap-2">
                <Skeleton className="size-3.5 shrink-0" />
                <Skeleton
                  shape="text"
                  size="sm"
                  className={index % 2 === 0 ? "w-28" : "w-20"}
                />
                <Skeleton shape="text" size="sm" className="w-5" />
              </span>
              <Skeleton
                shape="text"
                size="sm"
                className={index % 2 === 0 ? "w-24" : "w-16"}
              />
            </div>
          </TableCell>
        </TableRow>
      ))}
    </TableBody>
  );
}

function SubGroupPager({
  label,
  colSpan,
  page,
  pageSize,
  total,
  onPageChange,
}: {
  label: string;
  colSpan: number;
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}): React.ReactElement {
  return (
    <TableBody>
      <TableRow>
        <TableCell colSpan={colSpan} className="bg-sheet py-2">
          <nav
            aria-label={`${label} groups`}
            className="flex items-center justify-end gap-2 text-13 text-fg-muted"
          >
            <Pager
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={onPageChange}
              unit="groups"
              labelElement="span"
              previousLabel={`Previous ${label} groups`}
              nextLabel={`Next ${label} groups`}
              formatNumber={formatPagerNumber}
            />
          </nav>
        </TableCell>
      </TableRow>
    </TableBody>
  );
}

interface GroupSectionProps<TRow extends Row> extends GroupRenderProps<TRow> {
  measures: readonly GroupMeasure[];
  bucket: AggregateBucket;
  bucketKey: string;
  group: ResourceViewGroup | undefined;
  parentFilter: ListFilter;
  depth: number;
  remainingAxes: readonly GroupByDimension[];
  remainingGroups: readonly ResourceViewGroup[];
  pageSize: number;
  emptyMessage: ListEmptyContent;
  expanded: boolean;
  page: number;
  bodyId?: string;
  onToggle: (key: string) => void;
  onPageChange: (key: string, page: number) => void;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

const GroupSection = React.memo(GroupSectionInner) as typeof GroupSectionInner;

function GroupSectionInner<TRow extends Row>({
  resource,
  measures,
  bucket,
  bucketKey,
  group,
  parentFilter,
  depth,
  remainingAxes,
  remainingGroups,
  pageSize,
  emptyMessage,
  tableColumns,
  visibleColumns,
  columnVisibility,
  colSpan,
  resourceView,
  requestedFields,
  sortOrder,
  order,
  interactive,
  rowHref,
  onRowClick,
  modelMetadata = null,
  expanded,
  page,
  bodyId,
  onToggle,
  onPageChange,
  onListStateChange,
}: GroupSectionProps<TRow>): React.ReactElement {
  const headerId = React.useId();
  const regionId = React.useId();
  const bucketFilter = React.useMemo(
    () => bucketFilterForGroup(bucket, group, modelMetadata),
    [bucket, group, modelMetadata],
  );
  const expandable = bucketFilter !== undefined;
  const active = expanded && expandable;
  const label = bucketLabel(bucket, group, modelMetadata);
  const cumulativeFilter = React.useMemo(
    () => Filter.combine(parentFilter, bucketFilter),
    [bucketFilter, parentFilter],
  );
  const branch = remainingAxes.length > 0;
  const measuresByColumn = React.useMemo(
    () => new Map(measures.map((measure) => [measure.columnId, measure])),
    [measures],
  );

  return (
    <>
      <TableBody id={bodyId}>
        <TableRow>
          <TableCell className="h-9 w-8 bg-sheet-2 p-0">
            <button
              id={headerId}
              type="button"
              className={cn(
                "flex min-h-9 w-full items-center justify-center px-2 text-left text-13 outline-none",
                "focus-visible:focus-ring",
                expandable
                  ? "text-fg hover:bg-inset"
                  : "cursor-not-allowed text-fg-muted",
              )}
              aria-label={label}
              aria-expanded={expandable ? expanded : false}
              aria-controls={expandable ? regionId : undefined}
              aria-disabled={!expandable}
              onClick={() => {
                if (expandable) onToggle(bucketKey);
              }}
            >
              {expanded && expandable ? (
                <Glyph name="chevron-down" className="size-3.5 shrink-0 text-fg-muted" />
              ) : (
                <Glyph name="chevron-right" className="size-3.5 shrink-0 text-fg-muted" />
              )}
            </button>
          </TableCell>
          {visibleColumns.map((column, index) => {
            const measure = measuresByColumn.get(column.id);
            const value = measure ? measureValue(bucket, measure) : undefined;
            const formatted = measure && value != null
              ? formatMeasure(value, measure)
              : "";
            return (
              <TableCell
                key={column.id}
                className={cn(
                  "h-9 bg-sheet-2 text-13",
                  ALIGN_CLASS[alignOf(column.columnDef)],
                  index === 0 ? "font-semibold" : "",
                )}
                style={index === 0 ? depthIndentStyle(depth) : undefined}
                aria-label={
                  measure
                    ? `${label} ${measure.label}${formatted ? `: ${formatted}` : ""}`
                    : undefined
                }
              >
                {measure ? (
                  formatted
                ) : index === 0 ? (
                  <span className="inline-flex min-w-0 max-w-full items-center gap-2">
                    <span className="min-w-0 truncate">{label}</span>
                    <CountBadge value={bucket.count} />
                    {!expandable ? (
                      <span className="text-13 font-normal text-fg-muted">
                        Items unavailable
                      </span>
                    ) : null}
                  </span>
                ) : null}
              </TableCell>
            );
          })}
        </TableRow>
      </TableBody>
      {branch ? (
        <BranchGroupSection
          resource={resource}
          measures={measures}
          axes={remainingAxes}
          groups={remainingGroups}
          filter={cumulativeFilter}
          depth={depth + 1}
          pageSize={pageSize}
          expanded={active}
          regionId={regionId}
          tableColumns={tableColumns}
          columnVisibility={columnVisibility}
          colSpan={colSpan}
          resourceView={resourceView}
          requestedFields={requestedFields}
          sortOrder={sortOrder}
          order={order}
          interactive={interactive}
          rowHref={rowHref}
          onRowClick={onRowClick}
          emptyMessage={emptyMessage}
          modelMetadata={modelMetadata}
          visibleColumns={visibleColumns}
          onListStateChange={onListStateChange}
        />
      ) : (
        <LeafGroupSection
          resource={resource}
          modelMetadata={modelMetadata}
          bucket={bucket}
          bucketKey={bucketKey}
          label={label}
          filter={cumulativeFilter}
          expanded={active}
          page={page}
          regionId={regionId}
          tableColumns={tableColumns}
          columnVisibility={columnVisibility}
          colSpan={colSpan}
          resourceView={resourceView}
          requestedFields={requestedFields}
          sortOrder={sortOrder}
          order={order}
          interactive={interactive}
          rowHref={rowHref}
          visibleColumns={visibleColumns}
          onRowClick={onRowClick}
          onPageChange={onPageChange}
          onListStateChange={onListStateChange}
        />
      )}
    </>
  );
}

interface BranchGroupSectionProps<TRow extends Row> extends GroupRenderProps<TRow> {
  measures: readonly GroupMeasure[];
  axes: readonly GroupByDimension[];
  groups: readonly ResourceViewGroup[];
  filter: ListFilter;
  depth: number;
  pageSize: number;
  expanded: boolean;
  regionId: string;
  emptyMessage: ListEmptyContent;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

function BranchGroupSection<TRow extends Row>({
  resource,
  measures,
  axes,
  groups,
  filter,
  depth,
  pageSize,
  expanded,
  regionId,
  emptyMessage,
  tableColumns,
  visibleColumns,
  columnVisibility,
  colSpan,
  resourceView,
  requestedFields,
  sortOrder,
  order,
  interactive,
  rowHref,
  onRowClick,
  modelMetadata = null,
  onListStateChange,
}: BranchGroupSectionProps<TRow>): React.ReactElement | null {
  return (
    <GroupLevel
      resource={resource}
      measures={measures}
      axes={axes}
      groups={groups}
      filter={filter}
      depth={depth}
      page={1}
      pageSize={pageSize}
      enabled={expanded}
      regionId={regionId}
      tableColumns={tableColumns}
      visibleColumns={visibleColumns}
      columnVisibility={columnVisibility}
      colSpan={colSpan}
      resourceView={resourceView}
      requestedFields={requestedFields}
      sortOrder={sortOrder}
      order={order}
      interactive={interactive}
      rowHref={rowHref}
      onRowClick={onRowClick}
      emptyMessage={emptyMessage}
      modelMetadata={modelMetadata}
      onListStateChange={onListStateChange}
    />
  );
}

function GroupMeasureFooter<TRow extends Row>({
  table,
  measures,
  aggregate,
}: {
  table: TableModel<TRow>;
  measures: readonly GroupMeasure[];
  aggregate: AggregateBucket | null;
}): React.ReactElement {
  const byColumn = new Map(measures.map((measure) => [measure.columnId, measure]));
  return (
    <TableFooter>
      <TableRow>
        <TableCell className="w-8 text-fg-muted">{titleCase("total")}</TableCell>
        {table.getVisibleLeafColumns().map((column) => {
          const measure = byColumn.get(column.id);
          const value = measure && aggregate
            ? measureValue(aggregate, measure)
            : undefined;
          const formatted = measure && value != null
            ? formatMeasure(value, measure)
            : "";
          return (
            <TableCell
              key={column.id}
              className={ALIGN_CLASS[alignOf(column.columnDef)]}
              aria-label={
                measure
                  ? `Total ${measure.label}${formatted ? `: ${formatted}` : ""}`
                  : undefined
              }
            >
              {formatted}
            </TableCell>
          );
        })}
      </TableRow>
    </TableFooter>
  );
}

interface LeafGroupSectionProps<TRow extends Row> extends GroupRenderProps<TRow> {
  bucket: AggregateBucket;
  bucketKey: string;
  label: string;
  filter: ListFilter;
  expanded: boolean;
  page: number;
  regionId: string;
  onPageChange: (key: string, page: number) => void;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
}

function LeafGroupSection<TRow extends Row>({
  expanded,
  ...props
}: LeafGroupSectionProps<TRow>): React.ReactElement | null {
  if (!expanded) return null;
  return <LeafGroupRecords {...props} />;
}

function LeafGroupRecords<TRow extends Row>({
  resource,
  modelMetadata,
  bucket,
  bucketKey,
  label,
  filter,
  page,
  regionId,
  tableColumns,
  columnVisibility,
  colSpan,
  resourceView,
  requestedFields,
  sortOrder,
  order,
  interactive,
  rowHref,
  onRowClick,
  onPageChange,
  onListStateChange,
}: Omit<LeafGroupSectionProps<TRow>, "expanded">): React.ReactElement {
  const t = useBaseT();
  const pageCount = Math.max(
    1,
    Math.ceil(bucket.count / GROUPED_LIST_ITEM_PAGE_SIZE),
  );
  const currentPage = Math.min(page, pageCount);
  const dataResource = requireDataResource(resource, modelMetadata ?? null);
  const activeOrder = sortOrder ?? order;
  const listMeta = React.useMemo(
    () => ({ fields: refineFieldsFromPaths(requestedFields) }),
    [requestedFields],
  );
  const refineFilters = React.useMemo(
    () => crudFiltersFromFilterRecord(filter),
    [filter],
  );
  const refineSorters = React.useMemo(
    () => refineSortersFromAngeeOrder(activeOrder),
    [activeOrder],
  );
  const run = useList<RowRecord, HttpError>({
    resource: refineResourceName(dataResource),
    dataProviderName: dataResource.schemaName,
    pagination: {
      mode: "server",
      currentPage,
      pageSize: GROUPED_LIST_ITEM_PAGE_SIZE,
    },
    filters: refineFilters,
    sorters: refineSorters,
    meta: listMeta,
  });
  const navigationScope = React.useMemo(
    () => ({
      filter,
      order: activeOrder,
      page: currentPage,
      pageSize: GROUPED_LIST_ITEM_PAGE_SIZE,
    }),
    [activeOrder, currentPage, filter],
  );
  const list = React.useMemo(() => {
    const total = run.result.total;
    const localPageCount = total === undefined
      ? undefined
      : Math.max(1, Math.ceil(total / GROUPED_LIST_ITEM_PAGE_SIZE));
    return {
      rows: (run.result.data ?? []) as readonly Row[],
      total,
      pageCount: localPageCount,
      page: currentPage,
      pageSize: GROUPED_LIST_ITEM_PAGE_SIZE,
      pageInfo: undefined,
      hasNext: localPageCount !== undefined && currentPage < localPageCount,
      hasPrev: currentPage > 1,
      setPage: (next: number) => onPageChange(bucketKey, next),
      firstPage: () => onPageChange(bucketKey, 1),
      nextPage: () =>
        onPageChange(
          bucketKey,
          localPageCount
            ? Math.min(currentPage + 1, localPageCount)
            : currentPage + 1,
        ),
      prevPage: () => onPageChange(bucketKey, Math.max(1, currentPage - 1)),
      lastPage: () => {
        if (localPageCount) onPageChange(bucketKey, localPageCount);
      },
      fetching: run.query.isFetching,
      error: errorFromUnknown(run.query.error),
      refetch: () => {
        void run.query.refetch();
      },
    };
  }, [
    bucketKey,
    currentPage,
    onPageChange,
    run.query,
    run.result.data,
    run.result.total,
  ]);
  const listState = useResourceRowsSnapshot<TRow>(list, navigationScope);
  const handleRecordOpen = React.useCallback(
    (row: TRow) => {
      onListStateChange?.(listState);
    },
    [listState, onListStateChange],
  );
  const rows = list.rows as readonly TRow[];
  // Lazy per-group fetches need row models here; parent visibility is read-only.
  const table = useReactTable<TRow>({
    data: rows as TRow[],
    columns: tableColumns as ColumnDef<TRow>[],
    state: { columnVisibility },
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row, index) => rowPublicId(row) ?? String(index),
    autoResetPageIndex: false,
    autoResetExpanded: false,
  });
  const rowModels = table.getRowModel().rows;

  return (
    <TableBody id={regionId}>
      {list.error ? (
        <TableRow>
          <TableCell colSpan={colSpan} className="py-4 text-danger-text">
            {list.error.message}
          </TableCell>
        </TableRow>
      ) : list.fetching && rowModels.length === 0 ? (
        <ListSkeletonRows
          table={table}
          rowCount={Math.min(4, Math.max(1, bucket.count))}
          loadingLabel={t("list.loading")}
        />
      ) : rowModels.length === 0 ? (
        <TableRow>
          <TableCell
            colSpan={colSpan}
            className="py-4 text-center text-fg-muted"
          >
            No records in this group.
          </TableCell>
        </TableRow>
      ) : (
        rowModels.map((row) => (
          <RecordRow
            key={row.id}
            row={row}
            resourceView={resourceView}
            interactive={interactive}
            rowHref={rowHref}
            onRowClick={onRowClick}
            onRecordOpen={handleRecordOpen}
          />
        ))
      )}
      {!list.error && !list.fetching && bucket.count > 0 ? (
        <TableRow>
          <TableCell colSpan={colSpan} className="bg-sheet py-2">
            <nav
              aria-label={`${label} records`}
              className="flex items-center justify-end gap-2 text-13 text-fg-muted"
            >
              <Pager
                page={currentPage}
                pageSize={GROUPED_LIST_ITEM_PAGE_SIZE}
                total={bucket.count}
                onPageChange={(next) => onPageChange(bucketKey, next)}
                labelElement="span"
                previousLabel={`Previous ${label} records`}
                nextLabel={`Next ${label} records`}
                formatNumber={formatPagerNumber}
              />
            </nav>
          </TableCell>
        </TableRow>
      ) : null}
    </TableBody>
  );
}

function stableBucketKey(bucket: AggregateBucket): string {
  return stableSerialize(bucket.key ?? null);
}

function requireDataResource(
  resourceId: string,
  metadata: ModelMetadata | null,
): NonNullable<ModelMetadata["resource"]> {
  const dataResource = metadata?.resource;
  if (!dataResource) {
    throw new Error(`Resource "${resourceId}" has no data resource metadata.`);
  }
  return dataResource;
}

function errorFromUnknown(error: unknown): Error | null {
  if (!error) return null;
  if (error instanceof Error) return error;
  const message = typeof error === "object" && "message" in error
    ? String((error as { message?: unknown }).message)
    : String(error);
  return new Error(message);
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

function depthIndentStyle(depth: number): React.CSSProperties | undefined {
  if (depth <= 0) return undefined;
  return { paddingLeft: `calc(0.75rem + ${depth * 1.25}rem)` };
}

function normaliseLocalPage(page: number): number {
  if (!Number.isFinite(page)) return 1;
  return Math.max(1, Math.floor(page));
}

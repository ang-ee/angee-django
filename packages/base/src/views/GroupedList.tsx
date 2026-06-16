// Server-driven grouped list body: folded group buckets recurse one axis at a
// time; only leaf buckets fetch records via the cumulative bucket filter echo.
// Imports the shared seam from ./ListInternals and Pager from ../ui/pager;
// must NOT import ListView (ListView depends on this module, not vice versa).
import * as React from "react";
import {
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type Table as TableModel,
  type VisibilityState,
} from "@tanstack/react-table";
import {
  useResourceAggregate,
  useResourceGroupBy,
  useResourceList,
  type AggregateBucket,
  type GroupByDimension,
  type ModelMetadata,
  type ResourceTypeName,
  type Row,
  type UseResourceListOptions,
} from "@angee/sdk";
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
import type { DataViewContextValue } from "./data-view-context";
import type {
  DataViewGroup,
  DataViewResourceOrder,
} from "./data-view-model";
import {
  groupPagerStatesEqual,
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
  bucketValueLabels,
  formatMeasure,
  groupMeasuresFromColumns,
  groupOrderByForSort,
  groupFieldLabel,
  measureValue,
  type GroupMeasure,
  type VisibleFieldOption,
} from "./ListInternals";
import type { ColumnDescriptor } from "./page";
import type { ListEmptyContent } from "./list-view-types";

const GROUPED_LIST_ITEM_PAGE_SIZE = 20;

type ListFilter = UseResourceListOptions<ResourceTypeName>["filter"];

function formatPagerNumber(value: number): string {
  return value.toLocaleString();
}

export interface GroupedListBodyProps<TRow extends Row> {
  model: string;
  columns: readonly ColumnDescriptor<TRow>[];
  table: TableModel<TRow>;
  tableColumns: readonly ColumnDef<TRow>[];
  columnVisibility: VisibilityState;
  visibleColumnCount: number;
  visibleFields?: readonly VisibleFieldOption[];
  onVisibleFieldToggle?: (id: string, visible: boolean) => void;
  dataView: DataViewContextValue;
  groupDimensions: readonly GroupByDimension[];
  requestedFields: readonly string[];
  mergedFilter: ListFilter;
  sortOrder: DataViewResourceOrder | undefined;
  order: UseResourceListOptions<ResourceTypeName>["order"];
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  emptyMessage: ListEmptyContent;
  modelMetadata?: ModelMetadata | null;
  onPagerStateChange: (state: GroupPagerState) => void;
}

export function GroupedListBody<TRow extends Row>({
  model,
  columns,
  table,
  tableColumns,
  columnVisibility,
  visibleColumnCount,
  visibleFields = [],
  onVisibleFieldToggle,
  dataView,
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
}: GroupedListBodyProps<TRow>): React.ReactElement {
  const colSpan = Math.max(1, visibleColumnCount + 1);
  const measures = React.useMemo(
    () => groupMeasuresFromColumns(columns),
    [columns],
  );
  const grandTotal = useResourceAggregate(model, {
    filter: mergedFilter,
    measures,
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
                    dataView={dataView}
                    visibleFields={visibleFields}
                    onVisibleFieldToggle={onVisibleFieldToggle}
                    withVisibleFields={index === group.headers.length - 1}
                  />
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <GroupLevel
            model={model}
            measures={measures}
            axes={groupDimensions}
            groups={dataView.state.groupStack}
            filter={mergedFilter}
            depth={0}
            page={dataView.state.page}
            pageSize={dataView.state.pageSize}
            enabled
            tableColumns={tableColumns}
            columnVisibility={columnVisibility}
            colSpan={colSpan}
            dataView={dataView}
            requestedFields={requestedFields}
            sortOrder={sortOrder}
            order={order}
            interactive={interactive}
            rowHref={rowHref}
            onRowClick={onRowClick}
            emptyMessage={emptyMessage}
            modelMetadata={modelMetadata}
            onPagerStateChange={handlePagerStateChange}
          />
          {measures.length > 0 ? (
            <GroupMeasureFooter
              table={table}
              measures={measures}
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
  model: string;
  tableColumns: readonly ColumnDef<TRow>[];
  columnVisibility: VisibilityState;
  colSpan: number;
  dataView: DataViewContextValue;
  requestedFields: readonly string[];
  sortOrder: DataViewResourceOrder | undefined;
  order: UseResourceListOptions<ResourceTypeName>["order"];
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  modelMetadata?: ModelMetadata | null;
}

interface GroupLevelProps<TRow extends Row> extends GroupRenderProps<TRow> {
  measures: readonly GroupMeasure[];
  axes: readonly GroupByDimension[];
  groups: readonly DataViewGroup[];
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
  model,
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
  columnVisibility,
  colSpan,
  dataView,
  requestedFields,
  sortOrder,
  order,
  interactive,
  rowHref,
  onRowClick,
  modelMetadata = null,
}: GroupLevelProps<TRow>): React.ReactElement | null {
  const t = useBaseT();
  const axis = axes[0];
  const currentGroup = groups[0];
  const dimensions = React.useMemo(
    () => (axis ? [axis] : []),
    [axis],
  );
  const remainingAxes = React.useMemo(() => axes.slice(1), [axes]);
  const remainingGroups = React.useMemo(() => groups.slice(1), [groups]);
  const [localPage, setLocalPage] = React.useState(1);
  const levelPage = depth === 0 ? page : localPage;
  const levelEnabled = enabled && dimensions.length > 0;
  const groupOrderBy = React.useMemo(
    () => groupOrderByForSort(dataView.state.sort, currentGroup),
    [currentGroup, dataView.state.sort],
  );
  const groupAggregation = useResourceGroupBy(model, {
    dimensions,
    filter,
    measures,
    orderBy: groupOrderBy,
    page: levelPage,
    pageSize,
    withFilterEcho: true,
    enabled: levelEnabled,
  });

  React.useEffect(() => {
    if (depth !== 0 || !onPagerStateChange) return;
    onPagerStateChange({
      total: groupAggregation.totalCount,
      fetching: groupAggregation.fetching,
      error: groupAggregation.error,
    });
  }, [
    depth,
    groupAggregation.error,
    groupAggregation.fetching,
    groupAggregation.totalCount,
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
    const pageCount = Math.max(1, Math.ceil(groupAggregation.totalCount / pageSize));
    setLocalPage((current) => Math.min(current, pageCount));
  }, [depth, enabled, groupAggregation.totalCount, pageSize]);

  // stableBucketKey maps are intentionally not pruned; old entries restore state when groups reappear.
  const [expandedKeys, setExpandedKeys] = React.useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [pageByKey, setPageByKey] = React.useState<Record<string, number>>({});
  const toggleExpanded = React.useCallback((key: string) => {
    setExpandedKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
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
    Math.ceil(groupAggregation.totalCount / pageSize),
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
            model={model}
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
            columnVisibility={columnVisibility}
            colSpan={colSpan}
            dataView={dataView}
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
          />
        );
      })}
      {depth > 0 && groupAggregation.totalCount > 0 ? (
        <SubGroupPager
          label={currentGroup ? groupFieldLabel(currentGroup.field) : "Sub-group"}
          colSpan={colSpan}
          page={currentPage}
          pageSize={pageSize}
          total={groupAggregation.totalCount}
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
  group: DataViewGroup | undefined;
  parentFilter: ListFilter;
  depth: number;
  remainingAxes: readonly GroupByDimension[];
  remainingGroups: readonly DataViewGroup[];
  pageSize: number;
  emptyMessage: ListEmptyContent;
  expanded: boolean;
  page: number;
  bodyId?: string;
  onToggle: (key: string) => void;
  onPageChange: (key: string, page: number) => void;
}

function GroupSection<TRow extends Row>({
  model,
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
  columnVisibility,
  colSpan,
  dataView,
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
}: GroupSectionProps<TRow>): React.ReactElement {
  const headerId = React.useId();
  const regionId = React.useId();
  const expandable = bucket.filter !== undefined && bucket.filter !== null;
  const active = expanded && expandable;
  const label = bucketLabel(bucket, group, modelMetadata);
  const cumulativeFilter = React.useMemo(
    () => combineFilters(parentFilter, bucket.filter),
    [bucket.filter, parentFilter],
  );
  const branch = remainingAxes.length > 0;

  return (
    <>
      <TableBody id={bodyId}>
        <TableRow>
          <TableCell colSpan={colSpan} className="h-9 bg-sheet-2 p-0">
            <button
              id={headerId}
              type="button"
              className={cn(
                "flex min-h-9 w-full min-w-0 items-center gap-3 px-3 py-1.5 text-left text-13 outline-none",
                "focus-visible:focus-ring",
                expandable
                  ? "text-fg hover:bg-inset"
                  : "cursor-not-allowed text-fg-muted",
              )}
              style={depthIndentStyle(depth)}
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
              <span className="min-w-0 flex-1 truncate font-semibold">
                {label}
              </span>
              <span className="inline-flex shrink-0 items-center gap-2">
                <CountBadge value={bucket.count} />
                {measures.map((measure) => (
                  <GroupMeasureValue
                    key={`${measure.op}:${measure.field}`}
                    bucket={bucket}
                    measure={measure}
                  />
                ))}
                {!expandable ? (
                  <span className="text-13 font-normal text-fg-muted">
                    Items unavailable
                  </span>
                ) : null}
              </span>
            </button>
          </TableCell>
        </TableRow>
      </TableBody>
      {branch ? (
        <BranchGroupSection
          model={model}
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
          dataView={dataView}
          requestedFields={requestedFields}
          sortOrder={sortOrder}
          order={order}
          interactive={interactive}
          rowHref={rowHref}
          onRowClick={onRowClick}
          emptyMessage={emptyMessage}
          modelMetadata={modelMetadata}
        />
      ) : (
        <LeafGroupSection
          model={model}
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
          dataView={dataView}
          requestedFields={requestedFields}
          sortOrder={sortOrder}
          order={order}
          interactive={interactive}
          rowHref={rowHref}
          onRowClick={onRowClick}
          onPageChange={onPageChange}
        />
      )}
    </>
  );
}

interface BranchGroupSectionProps<TRow extends Row> extends GroupRenderProps<TRow> {
  measures: readonly GroupMeasure[];
  axes: readonly GroupByDimension[];
  groups: readonly DataViewGroup[];
  filter: ListFilter;
  depth: number;
  pageSize: number;
  expanded: boolean;
  regionId: string;
  emptyMessage: ListEmptyContent;
}

function BranchGroupSection<TRow extends Row>({
  model,
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
  columnVisibility,
  colSpan,
  dataView,
  requestedFields,
  sortOrder,
  order,
  interactive,
  rowHref,
  onRowClick,
  modelMetadata = null,
}: BranchGroupSectionProps<TRow>): React.ReactElement | null {
  return (
    <GroupLevel
      model={model}
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
      columnVisibility={columnVisibility}
      colSpan={colSpan}
      dataView={dataView}
      requestedFields={requestedFields}
      sortOrder={sortOrder}
      order={order}
      interactive={interactive}
      rowHref={rowHref}
      onRowClick={onRowClick}
      emptyMessage={emptyMessage}
      modelMetadata={modelMetadata}
    />
  );
}

function GroupMeasureValue({
  bucket,
  measure,
}: {
  bucket: AggregateBucket;
  measure: GroupMeasure;
}): React.ReactElement | null {
  const value = measureValue(bucket, measure);
  if (value == null) return null;
  return (
    <span className="text-13 font-normal tabular-nums text-fg-muted">
      {formatMeasure(value, measure)}
    </span>
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
}

function LeafGroupSection<TRow extends Row>({
  model,
  bucket,
  bucketKey,
  label,
  filter,
  expanded,
  page,
  regionId,
  tableColumns,
  columnVisibility,
  colSpan,
  dataView,
  requestedFields,
  sortOrder,
  order,
  interactive,
  rowHref,
  onRowClick,
  onPageChange,
}: LeafGroupSectionProps<TRow>): React.ReactElement | null {
  const t = useBaseT();
  const pageCount = Math.max(
    1,
    Math.ceil(bucket.count / GROUPED_LIST_ITEM_PAGE_SIZE),
  );
  const currentPage = Math.min(page, pageCount);
  const list = useResourceList(model, {
    fields: requestedFields,
    filter,
    order: sortOrder ?? order,
    page: currentPage,
    pageSize: GROUPED_LIST_ITEM_PAGE_SIZE,
    enabled: expanded,
  });
  const rows = list.rows as readonly TRow[];
  // Lazy per-group fetches need row models here; parent visibility is read-only.
  const table = useReactTable<TRow>({
    data: rows as TRow[],
    columns: tableColumns as ColumnDef<TRow>[],
    state: { columnVisibility },
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row, index) =>
      typeof row.id === "string" ? row.id : String(index),
    autoResetPageIndex: false,
    autoResetExpanded: false,
  });
  const rowModels = table.getRowModel().rows;

  if (!expanded) return null;
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
            dataView={dataView}
            interactive={interactive}
            rowHref={rowHref}
            onRowClick={onRowClick}
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

function bucketLabel(
  bucket: AggregateBucket,
  group: DataViewGroup | undefined,
  metadata: ModelMetadata | null,
): string {
  if (!group) return "All records";
  const [label] = bucketValueLabels(bucket, [group], metadata);
  return label ?? "All records";
}

function combineFilters(
  left: ListFilter,
  right: AggregateBucket["filter"],
): ListFilter {
  const leftRecord = filterRecord(left);
  const rightRecord = filterRecord(right);
  if (!leftRecord || Object.keys(leftRecord).length === 0) return rightRecord;
  if (!rightRecord || Object.keys(rightRecord).length === 0) return leftRecord;
  return combineFilterRecords(leftRecord, rightRecord);
}

function combineFilterRecords(
  left: Record<string, unknown>,
  right: Record<string, unknown>,
): Record<string, unknown> {
  const next: Record<string, unknown> = { ...left };
  let andFilter: Record<string, unknown> | undefined;
  for (const [key, value] of Object.entries(right)) {
    if (!Object.prototype.hasOwnProperty.call(next, key)) {
      next[key] = value;
      continue;
    }
    if (stableSerialize(next[key]) === stableSerialize(value)) continue;
    andFilter = { ...andFilter, [key]: value };
  }
  if (!andFilter) return next;
  const existingAnd = filterRecord(next.AND);
  next.AND = existingAnd
    ? combineFilterRecords(existingAnd, andFilter)
    : andFilter;
  return next;
}

function filterRecord(filter: unknown): Record<string, unknown> | undefined {
  if (!filter || typeof filter !== "object" || Array.isArray(filter)) {
    return undefined;
  }
  return filter as Record<string, unknown>;
}

function depthIndentStyle(depth: number): React.CSSProperties | undefined {
  if (depth <= 0) return undefined;
  return { paddingLeft: `calc(0.75rem + ${depth * 1.25}rem)` };
}

function normaliseLocalPage(page: number): number {
  if (!Number.isFinite(page)) return 1;
  return Math.max(1, Math.floor(page));
}

function stableSerialize(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableSerialize).join(",")}]`;
  }
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableSerialize(record[key])}`)
      .join(",")}}`;
  }
  if (value === undefined) return "undefined";
  return JSON.stringify(value);
}

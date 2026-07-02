// Thin server-grouped list body: a windowed renderer over the `GroupedListItem`
// stream the grouped surface owns. It fetches nothing — `useGroupedResourceViewSurface`
// emits the per-level group headers, the expanded buckets' leaf record rows, and
// the per-group pagers; this file only paints them (composing the same RecordRow,
// Pager, and padding-row window the flat list uses). Imports the shared seam from
// ./resource-view-list-body and the surface types; must NOT import ListView (ListView depends
// on this module, not vice versa).
import * as React from "react";
import {
  type Row,
} from "@angee/metadata";
import {
  type Column as TableColumn,
  type ColumnDef,
  type Table as TableModel,
} from "@tanstack/react-table";
import { type Virtualizer } from "@tanstack/react-virtual";
import {
  type AggregateBucket,
} from "@angee/refine";
import type {
  ModelMetadata,
} from "@angee/metadata";
import { Glyph } from "../chrome/Glyph";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { CountBadge } from "../ui/badge";
import { Pager } from "../ui/pager";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";
import { textRoleVariants } from "../ui/text";
import type { ResourceViewContextValue } from "./resource-view-context";
import type { ResourceListSnapshot } from "./resource-view-surface";
import {
  ALIGN_CLASS,
  ListEmpty,
  ListHeaderCell,
  ListLoadingFooter,
  ListSkeletonRows,
  MeasureFooter,
  RecordRow,
  TABLE_SCROLL_STYLE,
  VirtualPaddingRow,
  alignOf,
  estimateGroupedItemSize,
  formatMeasure,
  groupMeasuresFromColumns,
  hasuraMeasuresFromGroupMeasures,
  measureValue,
  useVirtualWindow,
  type GroupedListItem,
  type GroupedRecordNav,
  type GroupMeasure,
  type VisibleFieldOption,
} from "./resource-view-list-body";
import type { ColumnDescriptor } from "./page";
import type { ListEmptyContent } from "./resource-view-types";

function formatPagerNumber(value: number): string {
  return value.toLocaleString();
}

export interface GroupedListBodyProps<TRow extends Row> {
  columns: readonly ColumnDescriptor<TRow>[];
  table: TableModel<TRow>;
  tableColumns: readonly ColumnDef<TRow>[];
  visibleColumnCount: number;
  visibleFields?: readonly VisibleFieldOption[];
  onVisibleFieldToggle?: (id: string, visible: boolean) => void;
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
  listItems: readonly GroupedListItem<TRow>[];
  tableScrollRef: React.RefObject<HTMLDivElement | null>;
  rowVirtualizer: Virtualizer<HTMLDivElement, Element>;
  footerAggregate: AggregateBucket | null;
  expandedKeys: ReadonlySet<string>;
  toggleGroup: (key: string) => void;
  setScopePage: (key: string, page: number) => void;
  selectedIds: ReadonlySet<string>;
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
  emptyContent: ListEmptyContent;
  fetching: boolean;
  error: Error | null;
}

export function GroupedListBody<TRow extends Row>({
  columns,
  table,
  visibleColumnCount,
  visibleFields = [],
  onVisibleFieldToggle,
  resourceView,
  modelMetadata = null,
  listItems,
  tableScrollRef,
  rowVirtualizer,
  footerAggregate,
  toggleGroup,
  setScopePage,
  interactive,
  rowHref,
  onRowClick,
  onListStateChange,
  emptyContent,
  fetching,
  error,
}: GroupedListBodyProps<TRow>): React.ReactElement {
  const t = useUiT();
  // Grouped mode keeps a sticky chevron column in place of the select-all box.
  const colSpan = Math.max(1, visibleColumnCount + 1);
  const measures = React.useMemo(
    () => groupMeasuresFromColumns(columns),
    [columns],
  );
  const queryMeasures = React.useMemo(
    () => hasuraMeasuresFromGroupMeasures(measures, modelMetadata),
    [measures, modelMetadata],
  );
  const measuresByColumn = React.useMemo(
    () => new Map(queryMeasures.map((measure) => [measure.columnId, measure])),
    [queryMeasures],
  );
  const visibleColumns = table.getVisibleLeafColumns();
  const { paddingTop, paddingBottom, visibleIndexes } = useVirtualWindow(
    rowVirtualizer,
    listItems.length,
    (index) => estimateGroupedItemSize(listItems[index]),
  );
  const recordNavByRowId = React.useMemo(() => {
    const map = new Map<string, GroupedRecordNav>();
    for (const item of listItems) {
      if (item.kind === "record") map.set(item.row.id, item.nav);
    }
    return map;
  }, [listItems]);
  const handleRecordOpen = React.useCallback(
    (row: TRow) => {
      const nav = recordNavByRowId.get(String(row.id));
      if (!nav) return;
      onListStateChange?.(snapshotFromNav<TRow>(nav));
    },
    [onListStateChange, recordNavByRowId],
  );

  return (
    <>
      <div ref={tableScrollRef} className="overflow-auto" style={TABLE_SCROLL_STYLE}>
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
          <TableBody>
            {fetching && listItems.length === 0 ? (
              <ListSkeletonRows table={table} loadingLabel={t("list.loading")} />
            ) : error && listItems.length === 0 ? (
              <TableRow>
                <TableCell colSpan={colSpan} className="py-6 text-danger-text">
                  {error.message}
                </TableCell>
              </TableRow>
            ) : listItems.length === 0 ? (
              <TableRow>
                <TableCell colSpan={colSpan} className="py-8 text-center text-fg-muted">
                  <ListEmpty>{emptyContent}</ListEmpty>
                </TableCell>
              </TableRow>
            ) : (
              <>
                {paddingTop > 0 ? (
                  <VirtualPaddingRow height={paddingTop} colSpan={colSpan} />
                ) : null}
                {visibleIndexes.map((index) => {
                  const item = listItems[index];
                  return item ? (
                    <GroupedItemRow
                      key={groupedItemKey(item)}
                      item={item}
                      colSpan={colSpan}
                      table={table}
                      visibleColumns={visibleColumns}
                      measuresByColumn={measuresByColumn}
                      resourceView={resourceView}
                      interactive={interactive}
                      rowHref={rowHref}
                      onRowClick={onRowClick}
                      onRecordOpen={handleRecordOpen}
                      onToggleGroup={toggleGroup}
                      onPageChange={setScopePage}
                      loadingLabel={t("list.loading")}
                    />
                  ) : null;
                })}
                {paddingBottom > 0 ? (
                  <VirtualPaddingRow height={paddingBottom} colSpan={colSpan} />
                ) : null}
              </>
            )}
          </TableBody>
          {measures.length > 0 && footerAggregate ? (
            <MeasureFooter
              table={table}
              measures={queryMeasures}
              aggregate={footerAggregate}
              selectable
              labelInSelectionColumn
            />
          ) : null}
        </Table>
      </div>
      {fetching && listItems.length > 0 ? <ListLoadingFooter /> : null}
    </>
  );
}

function groupedItemKey<TRow extends Row>(item: GroupedListItem<TRow>): string {
  switch (item.kind) {
    case "groupHeader":
      return `header:${item.bucketKey}`;
    case "record":
      return item.itemKey;
    case "pager":
      return `pager:${item.unit}:${item.pageKey}`;
    case "skeleton":
    case "status":
      return item.itemKey;
  }
}

function GroupedItemRow<TRow extends Row>({
  item,
  colSpan,
  table,
  visibleColumns,
  measuresByColumn,
  resourceView,
  interactive,
  rowHref,
  onRowClick,
  onRecordOpen,
  onToggleGroup,
  onPageChange,
  loadingLabel,
}: {
  item: GroupedListItem<TRow>;
  colSpan: number;
  table: TableModel<TRow>;
  visibleColumns: readonly TableColumn<TRow, unknown>[];
  measuresByColumn: ReadonlyMap<string, GroupMeasure>;
  resourceView: ResourceViewContextValue;
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  onRecordOpen: (row: TRow) => void;
  onToggleGroup: (key: string) => void;
  onPageChange: (key: string, page: number) => void;
  loadingLabel: React.ReactNode;
}): React.ReactElement {
  switch (item.kind) {
    case "groupHeader":
      return (
        <GroupedHeaderRow
          item={item}
          visibleColumns={visibleColumns}
          measuresByColumn={measuresByColumn}
          onToggle={onToggleGroup}
        />
      );
    case "record":
      return (
        <RecordRow
          row={item.row}
          selected={resourceView.state.selectedIds.has(item.row.id)}
          onToggleSelected={resourceView.toggleSelectedId}
          interactive={interactive}
          rowHref={rowHref}
          onRowClick={onRowClick}
          onRecordOpen={onRecordOpen}
        />
      );
    case "pager":
      return (
        <GroupedPagerRow item={item} colSpan={colSpan} onPageChange={onPageChange} />
      );
    case "skeleton":
      return (
        <ListSkeletonRows
          table={table}
          rowCount={item.rowCount}
          loadingLabel={loadingLabel}
        />
      );
    case "status":
      return <GroupedStatusRow item={item} colSpan={colSpan} />;
  }
}

function GroupedHeaderRow<TRow extends Row>({
  item,
  visibleColumns,
  measuresByColumn,
  onToggle,
}: {
  item: Extract<GroupedListItem<TRow>, { kind: "groupHeader" }>;
  visibleColumns: readonly TableColumn<TRow, unknown>[];
  measuresByColumn: ReadonlyMap<string, GroupMeasure>;
  onToggle: (key: string) => void;
}): React.ReactElement {
  const headerId = React.useId();
  const { bucket, bucketKey, depth, label, count, expandable, expanded } = item;
  return (
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
          aria-disabled={!expandable}
          onClick={() => {
            if (expandable) onToggle(bucketKey);
          }}
        >
          <Glyph
            name={expanded && expandable ? "chevron-down" : "chevron-right"}
            className="size-3.5 shrink-0 text-fg-muted"
          />
        </button>
      </TableCell>
      {visibleColumns.map((column, index) => {
        const measure = measuresByColumn.get(column.id);
        const value = measure ? measureValue(bucket, measure) : undefined;
        const formatted = measure && value != null ? formatMeasure(value, measure) : "";
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
                <CountBadge value={count} />
                {!expandable ? (
                  <span className={cn(textRoleVariants({ role: "meta" }), "font-normal")}>
                    Items unavailable
                  </span>
                ) : null}
              </span>
            ) : null}
          </TableCell>
        );
      })}
    </TableRow>
  );
}

function GroupedPagerRow<TRow extends Row>({
  item,
  colSpan,
  onPageChange,
}: {
  item: Extract<GroupedListItem<TRow>, { kind: "pager" }>;
  colSpan: number;
  onPageChange: (key: string, page: number) => void;
}): React.ReactElement {
  const { pageKey, label, page, pageSize, total, unit } = item;
  const navLabel = unit === "groups" ? `${label} groups` : `${label} records`;
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="bg-sheet py-2">
        <nav
          aria-label={navLabel}
          className={cn(textRoleVariants({ role: "meta" }), "flex items-center justify-end gap-2")}
        >
          <Pager
            page={page}
            pageSize={pageSize}
            total={total}
            onPageChange={(next) => onPageChange(pageKey, next)}
            unit={unit === "groups" ? "groups" : undefined}
            labelElement="span"
            previousLabel={`Previous ${navLabel}`}
            nextLabel={`Next ${navLabel}`}
            formatNumber={formatPagerNumber}
          />
        </nav>
      </TableCell>
    </TableRow>
  );
}

function GroupedStatusRow<TRow extends Row>({
  item,
  colSpan,
}: {
  item: Extract<GroupedListItem<TRow>, { kind: "status" }>;
  colSpan: number;
}): React.ReactElement {
  return (
    <TableRow>
      <TableCell
        colSpan={colSpan}
        className={cn(
          "py-4",
          item.tone === "danger" ? "text-danger-text" : "text-center text-fg-muted",
        )}
        style={depthIndentStyle(item.depth)}
      >
        {item.message}
      </TableCell>
    </TableRow>
  );
}

function snapshotFromNav<TRow extends Row>(
  nav: GroupedRecordNav,
): ResourceListSnapshot<TRow> {
  const pageCount =
    nav.total === undefined ? undefined : Math.max(1, Math.ceil(nav.total / nav.pageSize));
  return {
    rows: nav.rows as readonly TRow[],
    total: nav.total,
    page: nav.page,
    pageSize: nav.pageSize,
    pageCount,
    hasNext: pageCount !== undefined && nav.page < pageCount,
    hasPrev: nav.page > 1,
    fetching: nav.fetching,
    navigationScope: {
      filter: nav.filter,
      order: nav.order,
      page: nav.page,
      pageSize: nav.pageSize,
    },
  };
}

function depthIndentStyle(depth: number): React.CSSProperties | undefined {
  if (depth <= 0) return undefined;
  return { paddingLeft: `calc(0.75rem + ${depth * 1.25}rem)` };
}

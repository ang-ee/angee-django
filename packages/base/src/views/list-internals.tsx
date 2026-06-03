// Leaf row/column/cell primitives for the data-view list surfaces: record rows,
// `cellContent`, column building, and the group key/label helpers. Imports only
// ui/sdk/page leaves so parent view modules can depend on it without a cycle.
import * as React from "react";
import {
  flexRender,
  type Cell as TableCellModel,
  type Column as TableColumn,
  type ColumnDef,
  type Row as TableRowModel,
  type Table as TableModel,
} from "@tanstack/react-table";
import type { Virtualizer } from "@tanstack/react-virtual";
import { useNavigate } from "@tanstack/react-router";
import type {
  AggregateBucket,
  AggregateMeasure,
  AggregateMeasureOperator,
  GroupByDimension,
  GroupByOrder,
  Row,
} from "@angee/sdk";
import { format, formatDistanceToNow } from "date-fns";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
} from "lucide-react";

import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Chip } from "../ui/chip";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";
import type { DataViewContextValue } from "./data-view-context";
import type { DataViewGroup } from "./data-view-model";
import type {
  ColumnAggregate,
  ColumnDescriptor,
  PageColumnAlign,
} from "./page";

export type ColumnAlign = PageColumnAlign;
export type ListColumn<TRow extends Row = Row> = ColumnDescriptor<TRow>;

export type RowGroup<TRow extends Row> = {
  key: string;
  label: string | null;
  path: readonly string[];
  depth: number;
  rows: readonly TableRowModel<TRow>[];
  children: readonly RowGroup<TRow>[];
};

export interface GroupMeasure extends AggregateMeasure {
  columnId: string;
  label: string;
  unit: string;
}

export type ListRenderItem<TRow extends Row> =
  | { kind: "group"; group: RowGroup<TRow> }
  | { kind: "row"; row: TableRowModel<TRow> };

export const ALIGN_CLASS: Record<PageColumnAlign, string> = {
  left: "text-left",
  center: "text-center",
  right: "text-right",
};
export const LIST_VIEW_SCROLL_BUDGET = "calc(100vh - 12rem)";
export const TABLE_SCROLL_STYLE: React.CSSProperties = {
  maxHeight: LIST_VIEW_SCROLL_BUDGET,
};
export const GROUP_ROW_HEIGHT = 32;
export const RECORD_ROW_HEIGHT = 40;

export function SelectionBar({
  count,
  onClear,
  onDelete,
  deletePending = false,
}: {
  count: number;
  onClear: () => void;
  onDelete?: () => void;
  deletePending?: boolean;
}): React.ReactElement {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border-subtle bg-brand px-3 py-2 text-13 text-on-brand">
      <span>{count} selected</span>
      <div className="flex items-center gap-2">
        {onDelete ? (
          <Button
            type="button"
            variant="danger"
            size="sm"
            pending={deletePending}
            onClick={onDelete}
          >
            Delete
          </Button>
        ) : null}
        <Button type="button" variant="ghost" size="sm" onClick={onClear}>
          Clear
        </Button>
      </div>
    </div>
  );
}

export interface FlatListBodyProps<TRow extends Row> {
  columns: readonly ColumnDescriptor<TRow>[];
  table: TableModel<TRow>;
  rowModels: readonly TableRowModel<TRow>[];
  listItems: readonly ListRenderItem<TRow>[];
  tableScrollRef: React.RefObject<HTMLDivElement | null>;
  rowVirtualizer: Virtualizer<HTMLDivElement, Element>;
  visibleColumnCount: number;
  allPageSelected: boolean;
  somePageSelected: boolean;
  onPageSelectionChange: (checked: boolean) => void;
  dataView: DataViewContextValue;
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  emptyMessage: React.ReactNode;
  fetching: boolean;
}

export function FlatListBody<TRow extends Row>({
  columns,
  table,
  rowModels,
  listItems,
  tableScrollRef,
  rowVirtualizer,
  visibleColumnCount,
  allPageSelected,
  somePageSelected,
  onPageSelectionChange,
  dataView,
  interactive,
  rowHref,
  onRowClick,
  emptyMessage,
  fetching,
}: FlatListBodyProps<TRow>): React.ReactElement {
  const colSpan = Math.max(1, visibleColumnCount + 1);
  const measures = React.useMemo(
    () => groupMeasuresFromColumns(columns),
    [columns],
  );
  const virtualItems = rowVirtualizer.getVirtualItems();
  const visibleIndexes = virtualItems.length > 0
    ? virtualItems.map((item) => item.index)
    : listItems.slice(0, Math.min(listItems.length, 20)).map((_, index) => index);
  const firstVirtualItem = virtualItems[0];
  const lastVirtualItem = virtualItems[virtualItems.length - 1];
  const paddingTop = firstVirtualItem?.start ?? 0;
  const paddingBottom = Math.max(
    0,
    virtualItems.length > 0
      ? rowVirtualizer.getTotalSize() - (lastVirtualItem?.end ?? 0)
      : estimatedListHeight(listItems.slice(visibleIndexes.length)),
  );
  return (
    <div
      ref={tableScrollRef}
      className="overflow-auto"
      style={TABLE_SCROLL_STYLE}
    >
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((group) => (
            <TableRow key={group.id}>
              <TableHead sticky className="w-8">
                <Checkbox
                  size="sm"
                  aria-label="Select all rows on this page"
                  checked={allPageSelected}
                  indeterminate={!allPageSelected && somePageSelected}
                  onCheckedChange={onPageSelectionChange}
                />
              </TableHead>
              {group.headers.map((header) => (
                <TableHead
                  sticky
                  key={header.id}
                  className={ALIGN_CLASS[alignOf(header.column.columnDef)]}
                >
                  {flexRender(
                    header.column.columnDef.header,
                    header.getContext(),
                  )}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {rowModels.length === 0 && !fetching ? (
            <TableRow>
              <TableCell
                colSpan={colSpan}
                className="py-8 text-center text-fg-muted"
              >
                {emptyMessage}
              </TableCell>
            </TableRow>
          ) : (
            <>
              {paddingTop > 0 ? (
                <VirtualPaddingRow
                  height={paddingTop}
                  colSpan={colSpan}
                />
              ) : null}
              {visibleIndexes.map((index) => {
                const item = listItems[index];
                return item
                  ? renderListItem({
                      item,
                      colSpan,
                      dataView,
                      interactive,
                      rowHref,
                      onRowClick,
                      measures,
                    })
                  : null;
              })}
              {paddingBottom > 0 ? (
                <VirtualPaddingRow
                  height={paddingBottom}
                  colSpan={colSpan}
                />
              ) : null}
            </>
          )}
        </TableBody>
      </Table>
    </div>
  );
}

export function buildColumns<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  dataView: DataViewContextValue,
): ColumnDef<TRow>[] {
  return columns.map((column) => ({
    id: column.field,
    header: () => (
      <SortHeader column={column} dataView={dataView}>
        {column.header ?? column.field}
      </SortHeader>
    ),
    cell: ({ row }) => cellContent(column, row.original),
    meta: {
      align: column.align ?? "left",
      label: column.header ?? column.field,
      field: column.field,
      aggregate: column.aggregate,
    },
  }));
}

function SortHeader<TRow extends Row>({
  column,
  dataView,
  children,
}: {
  column: ColumnDescriptor<TRow>;
  dataView: DataViewContextValue;
  children: React.ReactNode;
}): React.ReactElement {
  if (column.sortable === false) return <>{children}</>;
  const sort = dataView.state.sort;
  const active = sort?.field === column.field;
  const Icon = !active ? ArrowUpDown : sort.dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <button
      type="button"
      className="inline-flex min-w-0 items-center gap-1 rounded text-left outline-none hover:text-fg focus-visible:focus-ring"
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
      onClick={() => dataView.setSort(nextSort(sort, column.field))}
    >
      <span className="truncate">{children}</span>
      <Icon className="size-3 text-fg-subtle" aria-hidden />
    </button>
  );
}

export function RecordRow<TRow extends Row>({
  row,
  dataView,
  interactive,
  rowHref,
  onRowClick,
}: {
  row: TableRowModel<TRow>;
  dataView: DataViewContextValue;
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
}): React.ReactElement {
  const href = rowHref?.(row.original);
  if (href) {
    return (
      <LinkedRecordRow
        row={row}
        dataView={dataView}
        href={href}
      />
    );
  }
  return (
    <PlainRecordRow
      row={row}
      dataView={dataView}
      interactive={interactive}
      onRowClick={onRowClick}
    />
  );
}

function LinkedRecordRow<TRow extends Row>({
  row,
  dataView,
  href,
}: {
  row: TableRowModel<TRow>;
  dataView: DataViewContextValue;
  href: string;
}): React.ReactElement {
  const id = row.id;
  const selected = dataView.state.selectedIds.has(id);
  const navigate = useNavigate();
  const openHref = React.useCallback(
    (event: React.MouseEvent<HTMLTableRowElement>) => {
      if (isInteractiveTarget(event.target)) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        window.open(href, "_blank", "noopener");
        return;
      }
      event.preventDefault();
      void navigate({ to: href });
    },
    [href, navigate],
  );
  return (
    <TableRow
      interactive
      role="link"
      tabIndex={0}
      data-selected={selected ? "" : undefined}
      onClick={openHref}
      onKeyDown={(event) => {
        if (event.key !== "Enter" || event.target !== event.currentTarget) {
          return;
        }
        event.preventDefault();
        void navigate({ to: href });
      }}
    >
      <TableCell className="w-8">
        <Checkbox
          size="sm"
          aria-label="Select row"
          checked={selected}
          onClick={(event) => event.stopPropagation()}
          onCheckedChange={(checked) =>
            dataView.toggleSelectedId(id, checked)
          }
        />
      </TableCell>
      {row.getVisibleCells().map((cell) => (
        <TableCell
          key={cell.id}
          className={ALIGN_CLASS[alignOf(cell.column.columnDef)]}
        >
          {renderCell(cell)}
        </TableCell>
      ))}
    </TableRow>
  );
}

function PlainRecordRow<TRow extends Row>({
  row,
  dataView,
  interactive,
  onRowClick,
}: {
  row: TableRowModel<TRow>;
  dataView: DataViewContextValue;
  interactive: boolean;
  onRowClick?: (row: TRow) => void;
}): React.ReactElement {
  const id = row.id;
  const selected = dataView.state.selectedIds.has(id);
  return (
    <TableRow
      interactive={interactive}
      data-selected={selected ? "" : undefined}
      onClick={onRowClick ? () => onRowClick(row.original) : undefined}
    >
      <TableCell className="w-8">
        <Checkbox
          size="sm"
          aria-label="Select row"
          checked={selected}
          onClick={(event) => event.stopPropagation()}
          onCheckedChange={(checked) =>
            dataView.toggleSelectedId(id, checked)
          }
        />
      </TableCell>
      {row.getVisibleCells().map((cell, index) => (
        <TableCell
          key={cell.id}
          className={ALIGN_CLASS[alignOf(cell.column.columnDef)]}
        >
          {interactive && index === 0 && onRowClick ? (
            <button
              type="button"
              className="block w-full min-w-0 rounded-sm text-left text-inherit outline-none focus-visible:focus-ring"
              aria-label={`Open ${rowActionLabelForTableColumn(cell.column, row.original)}`}
              onClick={(event) => {
                event.stopPropagation();
                onRowClick(row.original);
              }}
            >
              {renderCell(cell)}
            </button>
          ) : (
            renderCell(cell)
          )}
        </TableCell>
      ))}
    </TableRow>
  );
}

function renderListItem<TRow extends Row>({
  item,
  colSpan,
  dataView,
  interactive,
  rowHref,
  onRowClick,
  measures,
}: {
  item: ListRenderItem<TRow>;
  colSpan: number;
  dataView: DataViewContextValue;
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  measures: readonly GroupMeasure[];
}): React.ReactElement {
  if (item.kind === "group") {
    return (
      <GroupHeader
        key={`group:${item.group.key}`}
        label={item.group.label ?? ""}
        rows={item.group.rows}
        depth={item.group.depth}
        colSpan={colSpan}
        measures={measures}
      />
    );
  }
  return (
    <RecordRow
      key={item.row.id}
      row={item.row}
      dataView={dataView}
      interactive={interactive}
      rowHref={rowHref}
      onRowClick={onRowClick}
    />
  );
}

function VirtualPaddingRow({
  height,
  colSpan,
}: {
  height: number;
  colSpan: number;
}): React.ReactElement {
  return (
    <TableRow aria-hidden="true" className="border-0">
      <TableCell
        colSpan={colSpan}
        className="p-0"
        style={{ height }}
      />
    </TableRow>
  );
}

function GroupHeader<TRow extends Row>({
  label,
  rows,
  depth,
  colSpan,
  measures,
}: {
  label: string;
  rows: readonly TableRowModel<TRow>[];
  depth: number;
  colSpan: number;
  measures: readonly GroupMeasure[];
}): React.ReactElement {
  const rowCount = rows.length;
  const summaries = measures.flatMap((measure) => {
    const value = measureRows(rows, measure);
    return value == null ? [] : [formatMeasure(value, measure)];
  });
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="h-8 bg-sheet-2 py-1.5">
        <div className="flex items-center justify-between gap-3 text-13">
          <span
            className="inline-flex items-center gap-2 font-semibold text-fg"
            style={{ paddingLeft: `${depth * 1.25}rem` }}
          >
            <span>{label}</span>
            <span className="font-normal text-fg-muted">
              {rowCount.toLocaleString()}
            </span>
          </span>
          <span className="text-fg-muted">
            {summaries.join(" · ")}
          </span>
        </div>
      </TableCell>
    </TableRow>
  );
}

function estimatedListHeight<TRow extends Row>(
  items: readonly ListRenderItem<TRow>[],
): number {
  return items.reduce(
    (height, item) =>
      height + (item.kind === "group" ? GROUP_ROW_HEIGHT : RECORD_ROW_HEIGHT),
    0,
  );
}

export function dataViewGroupToAggregateDimension(
  group: DataViewGroup,
): GroupByDimension {
  return {
    field: graphQLEnumValue(group.field),
    key: aggregateKeyField(group),
    ...(group.granularity
      ? { granularity: group.granularity.toUpperCase() }
      : {}),
  };
}

export function groupOrderByForSort(
  sort: DataViewContextValue["state"]["sort"],
  group: DataViewGroup | undefined,
): readonly GroupByOrder[] | undefined {
  if (!sort || !group || sort.field !== group.field) return undefined;
  return [
    {
      field: groupOrderField(group),
      direction: sort.dir === "asc" ? "ASC" : "DESC",
    },
  ];
}

export function groupOrderField(group: DataViewGroup): string {
  const field = fieldToSnake(group.field);
  return group.granularity ? `${field}_${group.granularity}` : field;
}

function aggregateKeyField(group: DataViewGroup): string {
  return group.granularity
    ? `${group.field}${titleCase(group.granularity).replace(/\s+/g, "")}`
    : group.field;
}

export function bucketValueLabels(
  bucket: AggregateBucket,
  groupStack: readonly DataViewGroup[],
): string[] {
  return groupStack.map((group) => {
    const value = bucket.key?.[aggregateKeyField(group)];
    return groupKey(value, group);
  });
}

export function groupKey(value: unknown, group: DataViewGroup): string {
  if (value == null) return "No value";
  const date = parseDate(value);
  if (!date) return typeof value === "string" ? statusLabel(value) : String(value);
  if (group.granularity === "year") return String(date.getFullYear());
  if (group.granularity === "quarter") {
    const quarter = Math.floor(date.getMonth() / 3) + 1;
    return `Q${quarter} ${date.getFullYear()}`;
  }
  if (group.granularity === "month") {
    return format(date, "MMMM yyyy");
  }
  if (group.granularity === "week") {
    return `Week of ${format(date, "MMMM d, yyyy")}`;
  }
  return format(date, "MMMM d, yyyy");
}

export function cellContent<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  row: TRow,
): React.ReactNode {
  if (column.render) return column.render(row);
  const value = readPath(row, column.field);
  if (column.tone) {
    const label = value == null ? "" : String(value);
    const tone = column.tone[label] ?? "default";
    return <Badge variant={tone}>{label ? statusLabel(label) : "-"}</Badge>;
  }
  if (Array.isArray(value)) {
    return (
      <span className="inline-flex min-w-0 flex-wrap items-center gap-1">
        {value.map((item, index) => (
          <Chip key={`${String(item)}:${index}`} tone="info" size="sm">
            {String(item)}
          </Chip>
        ))}
      </span>
    );
  }
  const date = looksLikeDateField(column.field) ? parseDate(value) : null;
  if (date) return formatDistanceToNow(date, { addSuffix: true });
  return displayValue(value);
}

function renderCell<TRow extends Row>(
  cell: TableCellModel<TRow, unknown>,
): React.ReactNode {
  return flexRender(cell.column.columnDef.cell, cell.getContext());
}

export function tableColumnLabel<TRow extends Row>(
  column: TableColumn<TRow, unknown>,
): React.ReactNode {
  return columnMeta(column.columnDef).label ?? column.id;
}

function rowActionLabelForTableColumn<TRow extends Row>(
  column: TableColumn<TRow, unknown>,
  row: TRow,
): string {
  const value = readPath(row, column.id);
  if (Array.isArray(value)) {
    const label = value.map((item) => String(item)).join(", ").trim();
    return label || "record";
  }
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return "record";
}

export function readPath(row: Row, path: string): unknown {
  let current: unknown = row;
  for (const key of path.split(".")) {
    if (current == null || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

export function groupMeasuresFromColumns<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
): readonly GroupMeasure[] {
  const measures: GroupMeasure[] = [];
  const seen = new Set<string>();
  for (const column of columns) {
    if (!isMeasureOperator(column.aggregate)) continue;
    const key = `${column.aggregate}:${column.field}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const label = columnLabel(column);
    measures.push({
      op: column.aggregate,
      field: column.field,
      columnId: column.field,
      label,
      unit: measureUnit(label),
    });
  }
  return measures;
}

function isMeasureOperator(
  aggregate: ColumnAggregate | undefined,
): aggregate is AggregateMeasureOperator {
  return (
    aggregate === "sum" ||
    aggregate === "avg" ||
    aggregate === "min" ||
    aggregate === "max"
  );
}

function columnLabel<TRow extends Row>(column: ColumnDescriptor<TRow>): string {
  const header = column.header;
  if (typeof header === "string") return header;
  if (typeof header === "number") return String(header);
  return titleCase(column.field);
}

function measureUnit(label: string): string {
  const normalized = label.trim();
  const countLabel = normalized.match(/^(.+)\s+count$/i)?.[1]?.trim();
  return pluralize((countLabel || normalized).toLowerCase());
}

function pluralize(value: string): string {
  if (value.endsWith("y") && !/[aeiou]y$/.test(value)) {
    return `${value.slice(0, -1)}ies`;
  }
  if (value.endsWith("s")) return value;
  return `${value}s`;
}

export function measureValue(
  bucket: AggregateBucket,
  measure: Pick<GroupMeasure, "op" | "field">,
): unknown {
  return bucket[measure.op]?.[measure.field];
}

export function formatMeasure(
  value: unknown,
  measure: Pick<GroupMeasure, "unit">,
): string {
  const formatted = formatMeasureValue(value);
  return measure.unit ? `${formatted} ${measure.unit}` : formatted;
}

function formatMeasureValue(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toLocaleString();
  }
  if (typeof value === "bigint") return value.toLocaleString();
  if (typeof value === "string" && /^-?\d+$/.test(value)) {
    return BigInt(value).toLocaleString();
  }
  return value == null ? "" : String(value);
}

function measureRows<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  measure: GroupMeasure,
): number | null {
  const values = rows
    .map((row) => numericValue(readPath(row.original, measure.field)))
    .filter((value): value is number => value !== null);
  if (values.length === 0) return null;
  if (measure.op === "sum") {
    return values.reduce((total, value) => total + value, 0);
  }
  if (measure.op === "avg") {
    return values.reduce((total, value) => total + value, 0) / values.length;
  }
  if (measure.op === "min") return Math.min(...values);
  return Math.max(...values);
}

function numericValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "bigint") return Number(value);
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function displayValue(value: unknown): React.ReactNode {
  if (value == null) return "";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function alignOf<TRow extends Row>(column: ColumnDef<TRow>): PageColumnAlign {
  return columnMeta(column).align ?? "left";
}

function columnMeta<TRow extends Row>(
  column: ColumnDef<TRow>,
): {
  align?: PageColumnAlign;
  label?: React.ReactNode;
  field?: string;
  aggregate?: ColumnAggregate;
} {
  return (
    column.meta as
      | {
          align?: PageColumnAlign;
          label?: React.ReactNode;
          field?: string;
          aggregate?: ColumnAggregate;
        }
      | undefined
  ) ?? {};
}

function nextSort(
  current: DataViewContextValue["state"]["sort"],
  field: string,
): DataViewContextValue["state"]["sort"] {
  if (current?.field !== field) return { field, dir: "asc" };
  if (current.dir === "asc") return { field, dir: "desc" };
  return null;
}

function isInteractiveTarget(target: EventTarget): boolean {
  return target instanceof HTMLElement
    && Boolean(
      target.closest(
        "a,button,input,select,textarea,label,[role='button'],[role='menuitem'],[role='checkbox']",
      ),
    );
}

function graphQLEnumValue(field: string): string {
  return fieldToSnake(field).toUpperCase();
}

function fieldToSnake(field: string): string {
  return field
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/[-\s]+/g, "_")
    .toLowerCase();
}

export function groupFieldLabel(field: string): string {
  const label = titleCase(field);
  return label.endsWith(" At") ? label.slice(0, -3) : label;
}

export function statusLabel(value: string): string {
  return titleCase(value.toLowerCase());
}

export function titleCase(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function looksLikeDateField(field: string): boolean {
  return /(?:At|Date|On)$/.test(field);
}

function parseDate(value: unknown): Date | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? null : date;
}

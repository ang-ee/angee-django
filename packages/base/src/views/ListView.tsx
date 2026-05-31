import * as React from "react";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type Row as TableRowModel,
} from "@tanstack/react-table";
import {
  useResourceList,
  type ResourceTypeName,
  type Row,
  type UseResourceListOptions,
  type UseResourceListResult,
} from "@angee/sdk";
import { formatDistanceToNow } from "date-fns";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";

import { DataToolbar } from "../toolbars";
import { Badge, type BadgeVariant } from "../ui/badge";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Chip } from "../ui/chip";
import { Spinner } from "../ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../ui/table";
import {
  DataViewProvider,
  useDataView,
  useDataViewMaybe,
  type DataViewContextValue,
} from "./data-view-context";
import {
  dataViewSortToResourceOrder,
  type DataViewFilter,
  type DataViewGroup,
} from "./data-view-model";
import type {
  ColumnDescriptor,
  PageColumnAlign,
} from "./page";

export type ColumnAlign = PageColumnAlign;
export type ListColumn<TRow extends Row = Row> = ColumnDescriptor<TRow>;

export interface ListViewProps<TRow extends Row = Row> {
  model: string;
  columns: readonly ColumnDescriptor<TRow>[];
  fields?: readonly string[];
  filter?: UseResourceListOptions<ResourceTypeName>["filter"];
  order?: UseResourceListOptions<ResourceTypeName>["order"];
  pageSize?: number;
  defaultGroup?: DataViewGroup | null;
  onCreate?: () => void;
  onRowClick?: (row: TRow) => void;
  rowHref?: (row: TRow) => string;
  emptyMessage?: React.ReactNode;
  className?: string;
}

const ALIGN_CLASS: Record<PageColumnAlign, string> = {
  left: "text-left",
  center: "text-center",
  right: "text-right",
};

export function ListView<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  const dataView = useDataViewMaybe();
  if (dataView) return <ListViewBody {...props} dataView={dataView} />;
  return (
    <DataViewProvider
      initialState={{
        pageSize: props.pageSize,
        group: props.defaultGroup ?? null,
      }}
    >
      <ListViewBound {...props} />
    </DataViewProvider>
  );
}

function ListViewBound<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  return <ListViewBody {...props} dataView={useDataView()} />;
}

function ListViewBody<TRow extends Row = Row>({
  model,
  columns,
  fields,
  filter,
  order,
  pageSize,
  defaultGroup,
  onCreate,
  onRowClick,
  rowHref,
  emptyMessage = "No records.",
  className,
  dataView,
}: ListViewProps<TRow> & {
  dataView: DataViewContextValue;
}): React.ReactElement {
  React.useEffect(() => {
    if (pageSize && dataView.state.pageSize !== pageSize) {
      dataView.setPageSize(pageSize);
    }
  }, [dataView, pageSize]);

  React.useEffect(() => {
    if (defaultGroup && dataView.state.group === null) {
      dataView.setGroup(defaultGroup);
    }
  }, [dataView, defaultGroup]);

  const requestedFields = React.useMemo(() => {
    const paths = new Set<string>(["id"]);
    for (const column of columns) paths.add(column.field);
    for (const extra of fields ?? []) paths.add(extra);
    return [...paths];
  }, [columns, fields]);

  const mergedFilter = React.useMemo(
    () => mergeFilters(filter, dataView.state.filter),
    [dataView.state.filter, filter],
  );
  const sortOrder = dataViewSortToResourceOrder(dataView.state.sort);
  const list = useResourceList(model, {
    fields: requestedFields,
    filter: mergedFilter,
    order: sortOrder ?? order,
    pageSize: dataView.state.pageSize,
    initialPage: dataView.state.page,
  });

  const tableColumns = React.useMemo(
    () => buildColumns(columns, dataView),
    [columns, dataView],
  );
  const rows = list.rows as readonly TRow[];
  const table = useReactTable<TRow>({
    data: rows as TRow[],
    columns: tableColumns,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row, index) =>
      typeof row.id === "string" ? row.id : String(index),
  });

  const rowModels = table.getRowModel().rows;
  const selectedIds = dataView.state.selectedIds;
  const pageIds = rows.flatMap((row, index) =>
    typeof row.id === "string" ? [row.id] : [String(index)],
  );
  const allPageSelected =
    pageIds.length > 0 && pageIds.every((id) => selectedIds.has(id));
  const somePageSelected = pageIds.some((id) => selectedIds.has(id));
  const groupedRows = groupRows(rowModels, dataView.state.group);

  const setPage = React.useCallback(
    (page: number) => {
      dataView.setPage(page);
      list.setPage(page);
    },
    [dataView, list],
  );

  const filterText = textFilterValue(dataView.state.filter);
  const interactive = Boolean(onRowClick || rowHref);

  return (
    <div
      className={[
        "overflow-hidden rounded-md border border-border bg-sheet",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <DataToolbar
        list={list}
        view={dataView.state.view}
        group={dataView.state.group}
        filterText={filterText}
        onCreate={onCreate}
        onClearGroup={() => dataView.setGroup(null)}
        onViewChange={dataView.setView}
        onFilterTextChange={(value) =>
          dataView.setFilter(nextTextFilter(dataView.state.filter, value))
        }
      />
      {selectedIds.size > 0 ? (
        <SelectionBar
          count={selectedIds.size}
          onClear={dataView.clearSelectedIds}
        />
      ) : null}
      {list.error ? (
        <div className="px-3 py-6 text-13 text-danger-text">
          {list.error.message}
        </div>
      ) : dataView.state.view === "board" ? (
        <BoardRows
          columns={columns}
          groups={groupedRows}
          emptyMessage={emptyMessage}
          rowHref={rowHref}
          onRowClick={onRowClick}
        />
      ) : (
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id}>
                <TableHead className="w-8">
                  <Checkbox
                    size="sm"
                    aria-label="Select all rows on this page"
                    checked={allPageSelected}
                    indeterminate={!allPageSelected && somePageSelected}
                    onCheckedChange={(checked) =>
                      setPageSelection(dataView, pageIds, checked)
                    }
                  />
                </TableHead>
                {group.headers.map((header) => (
                  <TableHead
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
            {rowModels.length === 0 && !list.fetching ? (
              <TableRow>
                <TableCell
                  colSpan={Math.max(1, columns.length + 1)}
                  className="py-8 text-center text-fg-muted"
                >
                  {emptyMessage}
                </TableCell>
              </TableRow>
            ) : (
              groupedRows.flatMap((group) => [
                ...(group.label !== null
                  ? [
                      <GroupHeader
                        key={`group:${group.label}`}
                        label={group.label}
                        rows={group.rows}
                        colSpan={columns.length + 1}
                      />,
                    ]
                  : []),
                ...group.rows.map((row) => (
                  <RecordRow
                    key={row.id}
                    row={row}
                    columns={columns}
                    dataView={dataView}
                    interactive={interactive}
                    rowHref={rowHref}
                    onRowClick={onRowClick}
                  />
                )),
              ])
            )}
          </TableBody>
        </Table>
      )}
      {list.fetching ? (
        <div className="flex items-center justify-center gap-2 border-t border-border px-3 py-4 text-13 text-fg-muted">
          <Spinner size="sm" />
          Loading...
        </div>
      ) : (
        <Pager list={list} onPageChange={setPage} />
      )}
    </div>
  );
}

function buildColumns<TRow extends Row>(
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
    meta: { align: column.align ?? "left" },
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

function RecordRow<TRow extends Row>({
  row,
  columns,
  dataView,
  interactive,
  rowHref,
  onRowClick,
}: {
  row: TableRowModel<TRow>;
  columns: readonly ColumnDescriptor<TRow>[];
  dataView: DataViewContextValue;
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
}): React.ReactElement {
  const id = row.id;
  const selected = dataView.state.selectedIds.has(id);
  const href = rowHref?.(row.original);
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
      {columns.map((column) => (
        <TableCell
          key={column.field}
          className={ALIGN_CLASS[column.align ?? "left"]}
        >
          {href ? (
            <a href={href} className="block text-inherit no-underline">
              {cellContent(column, row.original)}
            </a>
          ) : (
            cellContent(column, row.original)
          )}
        </TableCell>
      ))}
    </TableRow>
  );
}

function BoardRows<TRow extends Row>({
  columns,
  groups,
  emptyMessage,
  rowHref,
  onRowClick,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  groups: readonly RowGroup<TRow>[];
  emptyMessage: React.ReactNode;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
}): React.ReactElement {
  if (groups.every((group) => group.rows.length === 0)) {
    return <div className="px-3 py-8 text-center text-fg-muted">{emptyMessage}</div>;
  }
  return (
    <div className="grid gap-3 p-3 md:grid-cols-2 xl:grid-cols-3">
      {groups.flatMap((group) =>
        group.rows.map((row) => {
          const href = rowHref?.(row.original);
          const card = (
            <article className="grid gap-2 rounded-md border border-border-subtle bg-sheet p-3 shadow-xs">
              {columns.slice(0, 4).map((column, index) => (
                <div key={column.field} className="min-w-0">
                  {index === 0 ? (
                    <h3 className="truncate text-sm font-semibold text-fg">
                      {cellContent(column, row.original)}
                    </h3>
                  ) : (
                    <div className="flex min-w-0 items-center justify-between gap-3 text-13">
                      <span className="text-fg-muted">
                        {column.header ?? column.field}
                      </span>
                      <span className="min-w-0 truncate text-fg">
                        {cellContent(column, row.original)}
                      </span>
                    </div>
                  )}
                </div>
              ))}
            </article>
          );
          if (href) {
            return (
              <a key={row.id} href={href} className="block text-inherit no-underline">
                {card}
              </a>
            );
          }
          return (
            <button
              key={row.id}
              type="button"
              className="text-left"
              onClick={onRowClick ? () => onRowClick(row.original) : undefined}
            >
              {card}
            </button>
          );
        }),
      )}
    </div>
  );
}

function GroupHeader<TRow extends Row>({
  label,
  rows,
  colSpan,
}: {
  label: string;
  rows: readonly TableRowModel<TRow>[];
  colSpan: number;
}): React.ReactElement {
  const words = rows.reduce((total, row) => {
    const value = readPath(row.original, "wordCount");
    return total + (typeof value === "number" ? value : 0);
  }, 0);
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="bg-sheet-2 py-2">
        <div className="flex items-center justify-between gap-3 text-13">
          <span className="font-semibold text-fg">{label}</span>
          <span className="text-fg-muted">
            {rows.length} {rows.length === 1 ? "record" : "records"}
            {words > 0 ? ` · ${words.toLocaleString()} words` : ""}
          </span>
        </div>
      </TableCell>
    </TableRow>
  );
}

function SelectionBar({
  count,
  onClear,
}: {
  count: number;
  onClear: () => void;
}): React.ReactElement {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-border-subtle bg-brand px-3 py-2 text-13 text-on-brand">
      <span>{count} selected</span>
      <Button type="button" variant="ghost" size="sm" onClick={onClear}>
        Clear
      </Button>
    </div>
  );
}

function Pager({
  list,
  onPageChange,
}: {
  list: UseResourceListResult;
  onPageChange: (page: number) => void;
}): React.ReactElement {
  const pageCount = list.pageCount ?? 1;
  return (
    <div className="flex items-center justify-between gap-3 border-t border-border px-3 py-2 text-13 text-fg-muted">
      <span>{list.total ?? 0} total</span>
      <div className="flex items-center gap-2">
        <span>
          Page {list.page} of {pageCount}
        </span>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="iconSm"
            aria-label="First page"
            disabled={!list.hasPrev}
            onClick={() => onPageChange(1)}
          >
            <ChevronsLeft className="glyph" aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="iconSm"
            aria-label="Previous page"
            disabled={!list.hasPrev}
            onClick={() => onPageChange(Math.max(1, list.page - 1))}
          >
            <ChevronLeft className="glyph" aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="iconSm"
            aria-label="Next page"
            disabled={!list.hasNext}
            onClick={() => onPageChange(list.page + 1)}
          >
            <ChevronRight className="glyph" aria-hidden />
          </Button>
          <Button
            variant="ghost"
            size="iconSm"
            aria-label="Last page"
            disabled={!list.hasNext}
            onClick={() => {
              if (list.pageCount) onPageChange(list.pageCount);
            }}
          >
            <ChevronsRight className="glyph" aria-hidden />
          </Button>
        </div>
      </div>
    </div>
  );
}

type RowGroup<TRow extends Row> = {
  label: string | null;
  rows: readonly TableRowModel<TRow>[];
};

function groupRows<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  group: DataViewGroup | null,
): readonly RowGroup<TRow>[] {
  if (!group) return [{ label: null, rows }];
  const groups = new Map<string, TableRowModel<TRow>[]>();
  for (const row of rows) {
    const key = groupKey(readPath(row.original, group.field), group);
    const next = groups.get(key) ?? [];
    next.push(row);
    groups.set(key, next);
  }
  return [...groups.entries()].map(([label, groupRows]) => ({
    label,
    rows: groupRows,
  }));
}

function groupKey(value: unknown, group: DataViewGroup): string {
  if (value == null) return "No value";
  const date = parseDate(value);
  if (!date) return String(value);
  if (group.granularity === "year") return String(date.getFullYear());
  if (group.granularity === "month") {
    return date.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  }
  return date.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function cellContent<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  row: TRow,
): React.ReactNode {
  if (column.render) return column.render(row);
  const value = readPath(row, column.field);
  if (column.tone) {
    const label = value == null ? "" : String(value);
    const tone = column.tone[label] ?? "default";
    return <Badge variant={tone}>{label || "-"}</Badge>;
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

function readPath(row: Row, path: string): unknown {
  let current: unknown = row;
  for (const key of path.split(".")) {
    if (current == null || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[key];
  }
  return current;
}

function displayValue(value: unknown): React.ReactNode {
  if (value == null) return "";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function alignOf<TRow extends Row>(column: ColumnDef<TRow>): PageColumnAlign {
  const meta = column.meta as { align?: PageColumnAlign } | undefined;
  return meta?.align ?? "left";
}

function nextSort(
  current: DataViewContextValue["state"]["sort"],
  field: string,
): DataViewContextValue["state"]["sort"] {
  if (current?.field !== field) return { field, dir: "asc" };
  if (current.dir === "asc") return { field, dir: "desc" };
  return null;
}

function setPageSelection(
  dataView: DataViewContextValue,
  ids: readonly string[],
  checked: boolean,
): void {
  const next = new Set(dataView.state.selectedIds);
  for (const id of ids) {
    if (checked) next.add(id);
    else next.delete(id);
  }
  dataView.setSelectedIds(next);
}

function mergeFilters(
  base: UseResourceListOptions<ResourceTypeName>["filter"],
  view: DataViewFilter,
): UseResourceListOptions<ResourceTypeName>["filter"] {
  if (!base) return Object.keys(view).length > 0 ? view : undefined;
  return { ...base, ...view };
}

function textFilterValue(filter: DataViewFilter): string {
  const title = filter.title;
  if (!title || typeof title !== "object" || Array.isArray(title)) return "";
  const value = (title as Record<string, unknown>).iContains;
  return typeof value === "string" ? value : "";
}

function nextTextFilter(filter: DataViewFilter, value: string): DataViewFilter {
  const next = { ...filter };
  const trimmed = value.trim();
  if (trimmed) next.title = { iContains: trimmed };
  else delete next.title;
  return next;
}

function looksLikeDateField(field: string): boolean {
  return /(?:At|Date|On)$/.test(field);
}

function parseDate(value: unknown): Date | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? null : date;
}

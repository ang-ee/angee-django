import * as React from "react";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
  type Row as TableRowModel,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useNavigate } from "@tanstack/react-router";
import {
  useResourceList,
  type ResourceTypeName,
  type Row,
  type UseResourceListOptions,
  type UseResourceListResult,
} from "@angee/sdk";
import { format, formatDistanceToNow } from "date-fns";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
} from "lucide-react";

import {
  DataToolbar,
  type DataToolbarFilterOption,
  type DataToolbarGroupOption,
} from "../toolbars";
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
  dataViewGroupsEqual,
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
  createLabel?: React.ReactNode;
  onRowClick?: (row: TRow) => void;
  onListStateChange?: (state: ListViewState<TRow>) => void;
  rowHref?: (row: TRow) => string;
  emptyMessage?: React.ReactNode;
  className?: string;
}

export interface ListViewState<TRow extends Row = Row> {
  rows: readonly TRow[];
  total: number | undefined;
  page: number;
  pageSize: number;
  pageCount: number | undefined;
  hasNext: boolean;
  hasPrev: boolean;
  fetching: boolean;
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
  createLabel,
  onRowClick,
  onListStateChange,
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

  const handledDefaultGroupRef = React.useRef<DataViewGroup | null>(null);
  React.useEffect(() => {
    if (!defaultGroup) {
      handledDefaultGroupRef.current = null;
      return;
    }
    if (
      handledDefaultGroupRef.current
      && dataViewGroupsEqual(handledDefaultGroupRef.current, defaultGroup)
    ) {
      return;
    }
    handledDefaultGroupRef.current = defaultGroup;
    if (dataView.state.group === null) dataView.setGroup(defaultGroup);
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
  const listState = React.useMemo<ListViewState<TRow>>(
    () => ({
      rows,
      total: list.total,
      page: list.page,
      pageSize: list.pageSize,
      pageCount: list.pageCount,
      hasNext: list.hasNext,
      hasPrev: list.hasPrev,
      fetching: list.fetching,
    }),
    [
      rows,
      list.total,
      list.page,
      list.pageSize,
      list.pageCount,
      list.hasNext,
      list.hasPrev,
      list.fetching,
    ],
  );
  React.useEffect(() => {
    onListStateChange?.(listState);
  }, [listState, onListStateChange]);

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
  const groupedRows = React.useMemo(
    () => groupRows(rowModels, dataView.state.groupStack),
    [dataView.state.groupStack, rowModels],
  );
  const listItems = React.useMemo(
    () => flattenListItems(groupedRows),
    [groupedRows],
  );
  const tableScrollRef = React.useRef<HTMLDivElement | null>(null);
  const rowVirtualizer = useVirtualizer({
    count: listItems.length,
    getScrollElement: () => tableScrollRef.current,
    initialRect: { width: 1024, height: 600 },
    estimateSize: (index) =>
      listItems[index]?.kind === "group" ? GROUP_ROW_HEIGHT : RECORD_ROW_HEIGHT,
    overscan: 10,
  });
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
  const groupOptions = React.useMemo(
    () => buildGroupOptions(columns, defaultGroup),
    [columns, defaultGroup],
  );
  const filterOptions = React.useMemo(
    () => buildFilterOptions(columns, rows),
    [columns, rows],
  );
  const activeFilterIds = activeFilterIdsFor(
    dataView.state.filter,
    filterOptions,
  );

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
        groupStack={dataView.state.groupStack}
        groupOptions={groupOptions}
        filterOptions={filterOptions}
        activeFilterIds={activeFilterIds}
        filterText={filterText}
        createLabel={createLabel ?? createLabelForModel(model)}
        onCreate={onCreate}
        onClearGroup={() => dataView.setGroupStack([])}
        onGroupStackChange={dataView.setGroupStack}
        onViewChange={dataView.setView}
        onPageChange={setPage}
        onFilterToggle={(id) =>
          dataView.setFilter(
            nextFacetFilter(dataView.state.filter, filterOptions, id),
          )
        }
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
        <div
          ref={tableScrollRef}
          className="max-h-[calc(100vh-12rem)] overflow-auto"
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
                      onCheckedChange={(checked) =>
                        setPageSelection(dataView, pageIds, checked)
                      }
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
                <>
                  {paddingTop > 0 ? (
                    <VirtualPaddingRow
                      height={paddingTop}
                      colSpan={columns.length + 1}
                    />
                  ) : null}
                  {visibleIndexes.map((index) => {
                    const item = listItems[index];
                    return item
                      ? renderListItem({
                          item,
                          columns,
                          dataView,
                          interactive,
                          rowHref,
                          onRowClick,
                        })
                      : null;
                  })}
                  {paddingBottom > 0 ? (
                    <VirtualPaddingRow
                      height={paddingBottom}
                      colSpan={columns.length + 1}
                    />
                  ) : null}
                </>
              )}
            </TableBody>
          </Table>
        </div>
      )}
      {list.fetching ? (
        <div className="flex items-center justify-center gap-2 border-t border-border px-3 py-4 text-13 text-fg-muted">
          <Spinner size="sm" />
          Loading...
        </div>
      ) : null}
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
  if (href) {
    return (
      <LinkedRecordRow
        row={row}
        columns={columns}
        dataView={dataView}
        href={href}
      />
    );
  }
  return (
    <PlainRecordRow
      row={row}
      columns={columns}
      dataView={dataView}
      interactive={interactive}
      onRowClick={onRowClick}
    />
  );
}

function LinkedRecordRow<TRow extends Row>({
  row,
  columns,
  dataView,
  href,
}: {
  row: TableRowModel<TRow>;
  columns: readonly ColumnDescriptor<TRow>[];
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
      {columns.map((column, index) => (
        <TableCell
          key={column.field}
          className={ALIGN_CLASS[column.align ?? "left"]}
        >
          {cellContent(column, row.original)}
        </TableCell>
      ))}
    </TableRow>
  );
}

function PlainRecordRow<TRow extends Row>({
  row,
  columns,
  dataView,
  interactive,
  onRowClick,
}: {
  row: TableRowModel<TRow>;
  columns: readonly ColumnDescriptor<TRow>[];
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
      {columns.map((column, index) => (
        <TableCell
          key={column.field}
          className={ALIGN_CLASS[column.align ?? "left"]}
        >
          {interactive && index === 0 && onRowClick ? (
            <button
              type="button"
              className="block w-full min-w-0 rounded-sm text-left text-inherit outline-none focus-visible:focus-ring"
              aria-label={`Open ${rowActionLabel(column, row.original)}`}
              onClick={(event) => {
                event.stopPropagation();
                onRowClick(row.original);
              }}
            >
              {cellContent(column, row.original)}
            </button>
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
  const leaves = groups.flatMap(flattenLeaves);
  if (leaves.every((group) => group.rows.length === 0)) {
    return <div className="px-3 py-8 text-center text-fg-muted">{emptyMessage}</div>;
  }
  return (
    <div className="grid gap-3 p-3 md:grid-cols-2 xl:grid-cols-3">
      {leaves.flatMap((group) =>
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
              <BoardLinkCard
                key={row.id}
                href={href}
              >
                {card}
              </BoardLinkCard>
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

function BoardLinkCard({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}): React.ReactElement {
  const navigate = useNavigate();
  return (
    <button
      type="button"
      className="block w-full text-left text-inherit"
      onClick={() => {
        void navigate({ to: href });
      }}
    >
      {children}
    </button>
  );
}

const GROUP_ROW_HEIGHT = 32;
const RECORD_ROW_HEIGHT = 40;

type ListRenderItem<TRow extends Row> =
  | { kind: "group"; group: RowGroup<TRow> }
  | { kind: "row"; row: TableRowModel<TRow> };

function renderListItem<TRow extends Row>({
  item,
  columns,
  dataView,
  interactive,
  rowHref,
  onRowClick,
}: {
  item: ListRenderItem<TRow>;
  columns: readonly ColumnDescriptor<TRow>[];
  dataView: DataViewContextValue;
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
}): React.ReactElement {
  if (item.kind === "group") {
    return (
      <GroupHeader
        key={`group:${item.group.key}`}
        label={item.group.label ?? ""}
        rows={item.group.rows}
        depth={item.group.depth}
        colSpan={columns.length + 1}
      />
    );
  }
  return (
    <RecordRow
      key={item.row.id}
      row={item.row}
      columns={columns}
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
}: {
  label: string;
  rows: readonly TableRowModel<TRow>[];
  depth: number;
  colSpan: number;
}): React.ReactElement {
  const words = rows.reduce((total, row) => {
    const value = readPath(row.original, "wordCount");
    return total + (typeof value === "number" ? value : 0);
  }, 0);
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
              {rows.length.toLocaleString()}
            </span>
          </span>
          <span className="text-fg-muted">
            {words > 0 ? `${words.toLocaleString()} words` : ""}
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

type RowGroup<TRow extends Row> = {
  key: string;
  label: string | null;
  depth: number;
  rows: readonly TableRowModel<TRow>[];
  children: readonly RowGroup<TRow>[];
};

function groupRows<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  groupStack: readonly DataViewGroup[],
  depth = 0,
  parentKey = "root",
): readonly RowGroup<TRow>[] {
  const [group, ...rest] = groupStack;
  if (!group) {
    return [{ key: parentKey, label: null, depth, rows, children: [] }];
  }
  const groups = new Map<string, TableRowModel<TRow>[]>();
  for (const row of rows) {
    const key = groupKey(readPath(row.original, group.field), group);
    const next = groups.get(key) ?? [];
    next.push(row);
    groups.set(key, next);
  }
  return [...groups.entries()].map(([label, groupRows]) => ({
    key: `${parentKey}:${label}`,
    label,
    depth,
    rows: groupRows,
    children: groupRows.length > 0
      ? groupRowsByRest(groupRows, rest, depth + 1, `${parentKey}:${label}`)
      : [],
  }));
}

function groupRowsByRest<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  groupStack: readonly DataViewGroup[],
  depth: number,
  parentKey: string,
): readonly RowGroup<TRow>[] {
  return groupRows(rows, groupStack, depth, parentKey).filter(
    (group) => group.label !== null || group.children.length > 0,
  );
}

function flattenListItems<TRow extends Row>(
  groups: readonly RowGroup<TRow>[],
): ListRenderItem<TRow>[] {
  const output: ListRenderItem<TRow>[] = [];
  for (const group of groups) {
    if (group.label !== null) output.push({ kind: "group", group });
    if (group.children.length > 0) {
      output.push(...flattenListItems(group.children));
    } else {
      for (const row of group.rows) output.push({ kind: "row", row });
    }
  }
  return output;
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

function flattenLeaves<TRow extends Row>(group: RowGroup<TRow>): RowGroup<TRow>[] {
  if (group.children.length === 0) return [group];
  return group.children.flatMap(flattenLeaves);
}

function groupKey(value: unknown, group: DataViewGroup): string {
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

function cellContent<TRow extends Row>(
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

function rowActionLabel<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  row: TRow,
): string {
  const value = readPath(row, column.field);
  if (Array.isArray(value)) {
    const label = value.map((item) => String(item)).join(", ").trim();
    return label || "record";
  }
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return "record";
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

function isInteractiveTarget(target: EventTarget): boolean {
  return target instanceof HTMLElement
    && Boolean(
      target.closest(
        "a,button,input,select,textarea,label,[role='button'],[role='menuitem'],[role='checkbox']",
      ),
    );
}

function mergeFilters(
  base: UseResourceListOptions<ResourceTypeName>["filter"],
  view: DataViewFilter,
): UseResourceListOptions<ResourceTypeName>["filter"] {
  if (!base) return Object.keys(view).length > 0 ? view : undefined;
  return { ...base, ...view };
}

function buildGroupOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  defaultGroup: DataViewGroup | null | undefined,
): readonly DataToolbarGroupOption[] {
  const options: DataToolbarGroupOption[] = [];
  const seen = new Set<string>();
  const addOption = (option: DataToolbarGroupOption) => {
    if (seen.has(option.id)) return;
    seen.add(option.id);
    options.push(option);
  };

  if (defaultGroup) {
    addOption({
      id: defaultGroup.field,
      label: groupFieldLabel(defaultGroup.field),
      group: defaultGroup,
      type: looksLikeDateField(defaultGroup.field) ? "date" : "value",
    });
  }

  for (const column of columns) {
    if (looksLikeDateField(column.field)) {
      addOption({
        id: column.field,
        label: groupFieldLabel(column.field),
        group: { field: column.field, granularity: "day" },
        type: "date",
      });
      continue;
    }
    if (column.field === "status" || column.tone) {
      addOption({
        id: column.field,
        label: column.header ?? groupFieldLabel(column.field),
        group: { field: column.field },
        type: "value",
      });
    }
  }

  return options;
}

function buildFilterOptions<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  rows: readonly TRow[],
): readonly DataToolbarFilterOption[] {
  return columns.flatMap((column) => {
    if (column.field !== "status" && !column.tone) return [];
    return statusValues(column, rows).map((value) => ({
      id: `${column.field}:${value}`,
      label: statusLabel(value),
      chipLabel: statusLabel(value),
      filter: { [column.field]: { exact: value } },
    }));
  });
}

function statusValues<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  rows: readonly TRow[],
): string[] {
  const values = new Set<string>();
  if (column.tone) {
    for (const key of Object.keys(column.tone)) {
      if (key === key.toUpperCase()) values.add(key);
    }
  }
  if (values.size === 0) {
    for (const row of rows) {
      const value = readPath(row, column.field);
      if (typeof value === "string" && value.trim()) values.add(value);
    }
  }
  return [...values].sort(compareStatusValue);
}

const STATUS_ORDER = ["DRAFT", "IN_REVIEW", "ACTIVE", "ARCHIVED"];

function compareStatusValue(left: string, right: string): number {
  const leftIndex = STATUS_ORDER.indexOf(left.toUpperCase());
  const rightIndex = STATUS_ORDER.indexOf(right.toUpperCase());
  if (leftIndex !== -1 || rightIndex !== -1) {
    return (leftIndex === -1 ? Number.MAX_SAFE_INTEGER : leftIndex)
      - (rightIndex === -1 ? Number.MAX_SAFE_INTEGER : rightIndex);
  }
  return left.localeCompare(right);
}

function activeFilterIdsFor(
  filter: DataViewFilter,
  options: readonly DataToolbarFilterOption[],
): readonly string[] {
  return options.flatMap((option) => {
    const facet = facetFilter(option);
    if (!facet) return [];
    return statusFilterValues(filter, facet.field).includes(facet.value)
      ? [option.id]
      : [];
  });
}

function nextFacetFilter(
  filter: DataViewFilter,
  options: readonly DataToolbarFilterOption[],
  id: string,
): DataViewFilter {
  const option = options.find((candidate) => candidate.id === id);
  const facet = option ? facetFilter(option) : null;
  if (!facet) return filter;
  const current = statusFilterValues(filter, facet.field);
  const nextValues = current.includes(facet.value)
    ? current.filter((value) => value !== facet.value)
    : [...current, facet.value];
  const next = { ...filter };
  if (nextValues.length === 0) {
    delete next[facet.field];
  } else if (nextValues.length === 1) {
    next[facet.field] = { exact: nextValues[0] };
  } else {
    next[facet.field] = { inList: nextValues };
  }
  return next;
}

function facetFilter(
  option: DataToolbarFilterOption,
): { field: string; value: string } | null {
  const entry = Object.entries(option.filter)[0];
  if (!entry) return null;
  const [field, lookup] = entry;
  if (!field || !lookup || typeof lookup !== "object" || Array.isArray(lookup)) {
    return null;
  }
  const exact = (lookup as Record<string, unknown>).exact;
  return typeof exact === "string" ? { field, value: exact } : null;
}

function statusFilterValues(filter: DataViewFilter, field: string): readonly string[] {
  const lookup = filter[field];
  if (!lookup || typeof lookup !== "object" || Array.isArray(lookup)) return [];
  const exact = (lookup as Record<string, unknown>).exact;
  if (typeof exact === "string") return [exact];
  const inList = (lookup as Record<string, unknown>).inList;
  return Array.isArray(inList)
    ? inList.filter((value): value is string => typeof value === "string")
    : [];
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

function createLabelForModel(model: string): string {
  const name = model.split(".").at(-1) ?? "record";
  return `New ${groupFieldLabel(name).toLowerCase()}`;
}

function groupFieldLabel(field: string): string {
  const label = titleCase(field);
  return label.endsWith(" At") ? label.slice(0, -3) : label;
}

function statusLabel(value: string): string {
  return titleCase(value.toLowerCase());
}

function titleCase(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function looksLikeDateField(field: string): boolean {
  return /(?:At|Date|On)$/.test(field);
}

function parseDate(value: unknown): Date | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? null : date;
}

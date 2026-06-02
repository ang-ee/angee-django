import * as React from "react";
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type Cell as TableCellModel,
  type Column as TableColumn,
  type ColumnDef,
  type Row as TableRowModel,
  type Table as TableModel,
  type VisibilityState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useNavigate } from "@tanstack/react-router";
import {
  useResourceList,
  useResourceGroupBy,
  type AggregateBucket,
  type GroupByDimension,
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
  ChevronDown,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";

import {
  DataToolbar,
  type DataToolbarFilterOption,
  type DataToolbarGroupOption,
} from "../toolbars";
import type { DataToolbarVisibleField } from "../toolbars/DataToolbar";
import { cn } from "../lib/cn";
import { Badge, CountBadge, type BadgeVariant } from "../ui/badge";
import { Button } from "../ui/button";
import { Checkbox } from "../ui/checkbox";
import { Chip } from "../ui/chip";
import { Spinner } from "../ui/spinner";
import { StatusDot } from "../ui/status-icon";
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
const LIST_VIEW_SCROLL_BUDGET = "calc(100vh - 12rem)";
const TABLE_SCROLL_STYLE: React.CSSProperties = {
  maxHeight: LIST_VIEW_SCROLL_BUDGET,
};
const BOARD_SCROLL_STYLE: React.CSSProperties = {
  height: LIST_VIEW_SCROLL_BUDGET,
  maxHeight: LIST_VIEW_SCROLL_BUDGET,
};
const BOARD_CARD_SHELL_CLASS =
  "block w-full rounded-lg text-left text-inherit outline-none focus-visible:focus-ring";
const GROUPED_LIST_ITEM_PAGE_SIZE = 20;

export function ListView<TRow extends Row = Row>(
  props: ListViewProps<TRow>,
): React.ReactElement {
  const dataView = useDataViewMaybe();
  const initialState = React.useMemo(
    () => ({
      pageSize: props.pageSize,
    }),
    [props.pageSize],
  );
  if (dataView) return <ListViewBody {...props} dataView={dataView} />;
  return (
    <DataViewProvider initialState={initialState}>
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
  }, [dataView.setPageSize, dataView.state.pageSize, pageSize]);

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
  }, [dataView.setGroup, dataView.state.group, defaultGroup]);

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
  const groupDimensions = React.useMemo(
    () => dataView.state.groupStack.map(dataViewGroupToAggregateDimension),
    [dataView.state.groupStack],
  );
  const groupedListMode =
    dataView.state.view === "list" && groupDimensions.length > 0;
  const [groupPagerState, setGroupPagerState] =
    React.useState<GroupPagerState | null>(null);
  const handleGroupPagerStateChange = React.useCallback(
    (next: GroupPagerState) => {
      setGroupPagerState((current) =>
        groupPagerStatesEqual(current, next) ? current : next,
      );
    },
    [],
  );
  const groupAggregation = useResourceGroupBy(model, {
    dimensions: groupDimensions,
    filter: mergedFilter,
    enabled: dataView.state.view === "board" && groupDimensions.length > 0,
  });
  const groupCounts = React.useMemo(
    () => buildGroupCountMap(groupAggregation.buckets, dataView.state.groupStack),
    [dataView.state.groupStack, groupAggregation.buckets],
  );
  const sortOrder = dataViewSortToResourceOrder(dataView.state.sort);
  const list = useResourceList(model, {
    fields: requestedFields,
    filter: mergedFilter,
    order: sortOrder ?? order,
    pageSize: dataView.state.pageSize,
    page: dataView.state.page,
    enabled: !groupedListMode,
  });
  const toolbarList = React.useMemo<UseResourceListResult>(
    () =>
      groupedListMode
        ? groupPagerListState(dataView, groupPagerState)
        : list,
    [dataView, groupPagerState, groupedListMode, list],
  );

  const tableColumns = React.useMemo(
    () => buildColumns(columns, dataView),
    [columns, dataView],
  );
  const [columnVisibility, setColumnVisibility] =
    React.useState<VisibilityState>({});
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
    state: { columnVisibility },
    onColumnVisibilityChange: setColumnVisibility,
    getCoreRowModel: getCoreRowModel(),
    getRowId: (row, index) =>
      typeof row.id === "string" ? row.id : String(index),
    // Pagination/sort/filter/grouping are owned by the data-view (URL) state, not the
    // table. Without this, TanStack Table auto-resets its own page index whenever the
    // `data` reference changes; that reset fires `onStateChange` → re-render → new
    // `data` identity → reset again, an infinite loop that hard-locks WebKit when a
    // re-render storm (grouped rows + opening the filter popover) keeps it fed.
    autoResetPageIndex: false,
    autoResetExpanded: false,
  });
  const visibleColumnCount = table.getVisibleLeafColumns().length;
  const visibleFields = React.useMemo<readonly DataToolbarVisibleField[]>(
    () => {
      const visibleCount = table.getVisibleLeafColumns().length;
      return table.getAllLeafColumns().map((column) => {
        const visible = column.getIsVisible();
        return {
          id: column.id,
          label: tableColumnLabel(column),
          visible,
          disabled: visible && visibleCount <= 1,
        };
      });
    },
    [columnVisibility, table],
  );
  const toggleVisibleField = React.useCallback(
    (id: string, visible: boolean) => {
      const column = table.getColumn(id);
      if (!column) return;
      if (!visible && column.getIsVisible() && visibleColumnCount <= 1) return;
      column.toggleVisibility(visible);
    },
    [table, visibleColumnCount],
  );

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
    },
    [dataView.setPage],
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
        list={toolbarList}
        view={dataView.state.view}
        group={dataView.state.group}
        groupStack={dataView.state.groupStack}
        groupOptions={groupOptions}
        filterOptions={filterOptions}
        visibleFields={visibleFields}
        activeFilterIds={activeFilterIds}
        filterText={filterText}
        createLabel={createLabel ?? createLabelForModel(model)}
        onCreate={onCreate}
        onClearGroup={() => dataView.setGroupStack([])}
        onGroupStackChange={dataView.setGroupStack}
        onVisibleFieldToggle={toggleVisibleField}
        onViewChange={dataView.setView}
        onPageChange={setPage}
        pagerSubject={groupedListMode ? "Groups" : undefined}
        pagerTotalUnit={groupedListMode ? "groups" : undefined}
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
      {groupedListMode ? (
        <GroupedListBody
          model={model}
          table={table}
          tableColumns={tableColumns}
          columnVisibility={columnVisibility}
          visibleColumnCount={visibleColumnCount}
          dataView={dataView}
          groupDimensions={groupDimensions}
          requestedFields={requestedFields}
          mergedFilter={mergedFilter}
          sortOrder={sortOrder}
          order={order}
          interactive={interactive}
          rowHref={rowHref}
          onRowClick={onRowClick}
          emptyMessage={emptyMessage}
          onPagerStateChange={handleGroupPagerStateChange}
        />
      ) : list.error ? (
        <div className="px-3 py-6 text-13 text-danger-text">
          {list.error.message}
        </div>
      ) : dataView.state.view === "board" ? (
        <BoardRows
          columns={columns}
          groups={groupedRows}
          groupStack={dataView.state.groupStack}
          emptyMessage={emptyMessage}
          rowHref={rowHref}
          onRowClick={onRowClick}
        />
      ) : (
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
                    colSpan={Math.max(1, visibleColumnCount + 1)}
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
                      colSpan={Math.max(1, visibleColumnCount + 1)}
                    />
                  ) : null}
                  {visibleIndexes.map((index) => {
                    const item = listItems[index];
                    return item
                      ? renderListItem({
                          item,
                          colSpan: Math.max(1, visibleColumnCount + 1),
                          dataView,
                          interactive,
                          rowHref,
                          onRowClick,
                          groupCounts,
                        })
                      : null;
                  })}
                  {paddingBottom > 0 ? (
                    <VirtualPaddingRow
                      height={paddingBottom}
                      colSpan={Math.max(1, visibleColumnCount + 1)}
                    />
                  ) : null}
                </>
              )}
            </TableBody>
          </Table>
        </div>
      )}
      {!groupedListMode && list.fetching ? (
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
    meta: {
      align: column.align ?? "left",
      label: column.header ?? column.field,
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

function RecordRow<TRow extends Row>({
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

function BoardRows<TRow extends Row>({
  columns,
  groups,
  groupStack,
  emptyMessage,
  rowHref,
  onRowClick,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  groups: readonly RowGroup<TRow>[];
  groupStack: readonly DataViewGroup[];
  emptyMessage: React.ReactNode;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
}): React.ReactElement {
  const leaves = groups.flatMap(flattenLeaves);
  const groupFields = new Set(groupStack.map((group) => group.field));
  if (leaves.every((group) => group.rows.length === 0)) {
    return <div className="px-3 py-8 text-center text-fg-muted">{emptyMessage}</div>;
  }
  // Kanban is most useful with an active group axis; with no group-by applied a single lane is shown.
  // The board renders the current page only (bounded by the page-size cap, MAX_PAGE_SIZE), grouped into lanes; no row virtualization is used here.
  return (
    <div
      className="flex gap-3 overflow-x-auto overflow-y-hidden p-3"
      style={BOARD_SCROLL_STYLE}
    >
      {leaves.map((group) => (
        <BoardLane
          key={group.key}
          columns={columns}
          group={group}
          groupStack={groupStack}
          groupFields={groupFields}
          rowHref={rowHref}
          onRowClick={onRowClick}
        />
      ))}
    </div>
  );
}

function BoardLane<TRow extends Row>({
  columns,
  group,
  groupStack,
  groupFields,
  rowHref,
  onRowClick,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  group: RowGroup<TRow>;
  groupStack: readonly DataViewGroup[];
  groupFields: ReadonlySet<string>;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
}): React.ReactElement {
  const headingId = React.useId();
  const tone = laneDotTone(group, groupStack, columns);
  return (
    <section
      aria-labelledby={headingId}
      className="flex max-h-full min-h-0 w-[300px] flex-none flex-col rounded-[10px] border border-border-subtle bg-inset"
    >
      <div className="sticky top-0 z-10 flex items-center gap-2 rounded-t-[10px] bg-inset px-3 pt-3 pb-2">
        {tone ? <StatusDot tone={tone} /> : null}
        <h3
          id={headingId}
          className="min-w-0 flex-1 truncate text-13 font-semibold text-fg"
        >
          {group.label ?? "All records"}
        </h3>
        <CountBadge value={group.rows.length} />
      </div>
      <div className="flex min-h-0 flex-col gap-2 overflow-y-auto px-2 pb-2">
        {group.rows.map((row) => (
          <BoardRowCard
            key={row.id}
            columns={columns}
            groupFields={groupFields}
            row={row}
            rowHref={rowHref}
            onRowClick={onRowClick}
          />
        ))}
      </div>
    </section>
  );
}

function BoardRowCard<TRow extends Row>({
  columns,
  groupFields,
  row,
  rowHref,
  onRowClick,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  groupFields: ReadonlySet<string>;
  row: TableRowModel<TRow>;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
}): React.ReactElement {
  const href = rowHref?.(row.original);
  const cardColumns = columns
    .filter((column) => !groupFields.has(column.field))
    .slice(0, 4);
  const [titleColumn, ...detailColumns] = cardColumns;
  return (
    <BoardCardShell
      href={href}
      onClick={onRowClick ? () => onRowClick(row.original) : undefined}
    >
      <article className="grid gap-2 rounded-lg border border-border-subtle bg-sheet p-3 shadow-xs transition hover:-translate-y-0.5 hover:border-border hover:shadow-md">
        {titleColumn ? (
          <span className="block min-w-0 truncate text-sm font-semibold text-fg">
            {cellContent(titleColumn, row.original)}
          </span>
        ) : null}
        {detailColumns.map((column) => (
          <div
            key={column.field}
            className="flex min-w-0 items-start justify-between gap-3 text-13"
          >
            <span className="shrink-0 text-fg-muted">
              {column.header ?? column.field}
            </span>
            <span className="min-w-0 text-right text-fg">
              {cellContent(column, row.original)}
            </span>
          </div>
        ))}
      </article>
    </BoardCardShell>
  );
}

function BoardCardShell({
  href,
  onClick,
  children,
}: {
  href?: string;
  onClick?: () => void;
  children: React.ReactNode;
}): React.ReactElement {
  const navigate = useNavigate();
  const handleClick = React.useCallback(() => {
    if (href) {
      void navigate({ to: href });
      return;
    }
    onClick?.();
  }, [href, navigate, onClick]);
  return (
    <button
      type="button"
      role={href ? "link" : undefined}
      className={BOARD_CARD_SHELL_CLASS}
      onClick={handleClick}
    >
      {children}
    </button>
  );
}

function laneDotTone<TRow extends Row>(
  group: RowGroup<TRow>,
  groupStack: readonly DataViewGroup[],
  columns: readonly ColumnDescriptor<TRow>[],
): BadgeVariant | undefined {
  const groupField = groupStack[group.depth]?.field;
  const column = groupField
    ? columns.find((candidate) => candidate.field === groupField)
    : undefined;
  if (!groupField || !column?.tone) return undefined;
  const row = group.rows[0]?.original;
  const value = row ? readPath(row, groupField) : undefined;
  const label = value == null ? "" : String(value);
  return column.tone[label] ?? "default";
}

const GROUP_ROW_HEIGHT = 32;
const RECORD_ROW_HEIGHT = 40;

type ListRenderItem<TRow extends Row> =
  | { kind: "group"; group: RowGroup<TRow> }
  | { kind: "row"; row: TableRowModel<TRow> };

function renderListItem<TRow extends Row>({
  item,
  colSpan,
  dataView,
  interactive,
  rowHref,
  onRowClick,
  groupCounts,
}: {
  item: ListRenderItem<TRow>;
  colSpan: number;
  dataView: DataViewContextValue;
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  groupCounts: ReadonlyMap<string, number>;
}): React.ReactElement {
  if (item.kind === "group") {
    return (
      <GroupHeader
        key={`group:${item.group.key}`}
        label={item.group.label ?? ""}
        rows={item.group.rows}
        count={groupCounts.get(groupPathKey(item.group.path))}
        depth={item.group.depth}
        colSpan={colSpan}
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
  count,
  depth,
  colSpan,
}: {
  label: string;
  rows: readonly TableRowModel<TRow>[];
  count: number | undefined;
  depth: number;
  colSpan: number;
}): React.ReactElement {
  const rowCount = count ?? rows.length;
  const words = count === undefined
    ? rows.reduce((total, row) => {
        const value = readPath(row.original, "wordCount");
        return total + (typeof value === "number" ? value : 0);
      }, 0)
    : 0;
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
            {words > 0 ? `${words.toLocaleString()} words` : ""}
          </span>
        </div>
      </TableCell>
    </TableRow>
  );
}

interface GroupPagerState {
  total: number;
  fetching: boolean;
  error: Error | null;
}

function groupPagerStatesEqual(
  left: GroupPagerState | null,
  right: GroupPagerState,
): boolean {
  return (
    left !== null &&
    left.total === right.total &&
    left.fetching === right.fetching &&
    left.error === right.error
  );
}

function groupPagerListState(
  dataView: DataViewContextValue,
  groupState: GroupPagerState | null,
): UseResourceListResult {
  const total = groupState?.total ?? 0;
  const page = dataView.state.page;
  const pageSize = dataView.state.pageSize;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const setPage = (next: number) => dataView.setPage(next);
  return {
    rows: [],
    total,
    pageCount,
    page,
    pageSize,
    pageInfo: undefined,
    hasNext: page < pageCount,
    hasPrev: page > 1,
    setPage,
    firstPage: () => setPage(1),
    nextPage: () => setPage(page + 1),
    prevPage: () => setPage(Math.max(1, page - 1)),
    lastPage: () => setPage(pageCount),
    fetching: groupState?.fetching ?? false,
    error: groupState?.error ?? null,
    refetch: () => {},
  };
}

function GroupedListBody<TRow extends Row>({
  model,
  table,
  tableColumns,
  columnVisibility,
  visibleColumnCount,
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
  onPagerStateChange,
}: {
  model: string;
  table: TableModel<TRow>;
  tableColumns: readonly ColumnDef<TRow>[];
  columnVisibility: VisibilityState;
  visibleColumnCount: number;
  dataView: DataViewContextValue;
  groupDimensions: readonly GroupByDimension[];
  requestedFields: readonly string[];
  mergedFilter: UseResourceListOptions<ResourceTypeName>["filter"];
  sortOrder: ReturnType<typeof dataViewSortToResourceOrder>;
  order: UseResourceListOptions<ResourceTypeName>["order"];
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  emptyMessage: React.ReactNode;
  onPagerStateChange: (state: GroupPagerState) => void;
}): React.ReactElement {
  const groupAggregation = useResourceGroupBy(model, {
    dimensions: groupDimensions,
    filter: mergedFilter,
    page: dataView.state.page,
    pageSize: dataView.state.pageSize,
    withFilterEcho: true,
  });
  React.useEffect(() => {
    onPagerStateChange({
      total: groupAggregation.totalCount,
      fetching: groupAggregation.fetching,
      error: groupAggregation.error,
    });
  }, [
    groupAggregation.error,
    groupAggregation.fetching,
    groupAggregation.totalCount,
    onPagerStateChange,
  ]);

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
  const setGroupPage = React.useCallback((key: string, page: number) => {
    setPageByKey((current) => ({
      ...current,
      [key]: normaliseLocalPage(page),
    }));
  }, []);
  const colSpan = Math.max(1, visibleColumnCount + 1);
  const hasBuckets = groupAggregation.buckets.length > 0;

  return (
    <>
      <div className="overflow-auto" style={TABLE_SCROLL_STYLE}>
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id}>
                {/* Grouped mode omits page-level select-all; per-row selection still works. */}
                <TableHead sticky className="w-8" />
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
          {groupAggregation.error ? (
            <TableBody>
              <TableRow>
                <TableCell
                  colSpan={colSpan}
                  className="py-6 text-danger-text"
                >
                  {groupAggregation.error.message}
                </TableCell>
              </TableRow>
            </TableBody>
          ) : !hasBuckets && !groupAggregation.fetching ? (
            <TableBody>
              <TableRow>
                <TableCell
                  colSpan={colSpan}
                  className="py-8 text-center text-fg-muted"
                >
                  {emptyMessage}
                </TableCell>
              </TableRow>
            </TableBody>
          ) : (
            groupAggregation.buckets.map((bucket) => {
              const key = stableBucketKey(bucket);
              return (
                <GroupSection
                  key={key}
                  model={model}
                  bucket={bucket}
                  bucketKey={key}
                  groupStack={dataView.state.groupStack}
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
                  expanded={expandedKeys.has(key)}
                  page={pageByKey[key] ?? 1}
                  onToggle={toggleExpanded}
                  onPageChange={setGroupPage}
                />
              );
            })
          )}
        </Table>
      </div>
      {groupAggregation.fetching ? (
        <div className="flex items-center justify-center gap-2 border-t border-border px-3 py-4 text-13 text-fg-muted">
          <Spinner size="sm" />
          Loading...
        </div>
      ) : null}
    </>
  );
}

function GroupSection<TRow extends Row>({
  model,
  bucket,
  bucketKey,
  groupStack,
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
  expanded,
  page,
  onToggle,
  onPageChange,
}: {
  model: string;
  bucket: AggregateBucket;
  bucketKey: string;
  groupStack: readonly DataViewGroup[];
  tableColumns: readonly ColumnDef<TRow>[];
  columnVisibility: VisibilityState;
  colSpan: number;
  dataView: DataViewContextValue;
  requestedFields: readonly string[];
  sortOrder: ReturnType<typeof dataViewSortToResourceOrder>;
  order: UseResourceListOptions<ResourceTypeName>["order"];
  interactive: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  expanded: boolean;
  page: number;
  onToggle: (key: string) => void;
  onPageChange: (key: string, page: number) => void;
}): React.ReactElement {
  const headerId = React.useId();
  const regionId = React.useId();
  const expandable = bucket.filter !== undefined && bucket.filter !== null;
  const label = bucketLabel(bucket, groupStack);
  const pageCount = Math.max(
    1,
    Math.ceil(bucket.count / GROUPED_LIST_ITEM_PAGE_SIZE),
  );
  const currentPage = Math.min(page, pageCount);
  const list = useResourceList(model, {
    fields: requestedFields,
    filter: bucket.filter ?? undefined,
    order: sortOrder ?? order,
    page: currentPage,
    pageSize: GROUPED_LIST_ITEM_PAGE_SIZE,
    enabled: expanded && expandable,
  });
  const rows = list.rows as readonly TRow[];
  // Lazy per-group fetches need row models here; onColumnVisibilityChange is omitted because parent visibility is read-only.
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

  return (
    <>
      <TableBody>
        <TableRow>
          <TableCell colSpan={colSpan} className="h-9 bg-sheet-2 p-0">
            <button
              id={headerId}
              type="button"
              className={cn(
                "flex min-h-9 w-full min-w-0 items-center gap-3 px-3 py-1.5 text-left text-13 outline-none focus-visible:focus-ring",
                expandable
                  ? "text-fg hover:bg-inset"
                  : "cursor-not-allowed text-fg-muted",
              )}
              aria-expanded={expandable ? expanded : false}
              aria-controls={expandable ? regionId : undefined}
              aria-disabled={!expandable}
              onClick={() => {
                if (expandable) onToggle(bucketKey);
              }}
            >
              {expanded && expandable ? (
                <ChevronDown className="size-3.5 shrink-0 text-fg-muted" aria-hidden />
              ) : (
                <ChevronRight className="size-3.5 shrink-0 text-fg-muted" aria-hidden />
              )}
              <span className="min-w-0 flex-1 truncate font-semibold">
                {label}
              </span>
              <span className="inline-flex shrink-0 items-center gap-2">
                <CountBadge value={bucket.count} />
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
      {expanded && expandable ? (
        <TableBody id={regionId}>
          {list.error ? (
            <TableRow>
              <TableCell colSpan={colSpan} className="py-4 text-danger-text">
                {list.error.message}
              </TableCell>
            </TableRow>
          ) : list.fetching ? (
            <TableRow>
              <TableCell colSpan={colSpan} className="py-4 text-fg-muted">
                <span className="inline-flex items-center gap-2">
                  <Spinner size="sm" />
                  Loading...
                </span>
              </TableCell>
            </TableRow>
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
                <GroupItemPager
                  label={label}
                  page={currentPage}
                  pageSize={GROUPED_LIST_ITEM_PAGE_SIZE}
                  total={bucket.count}
                  onPageChange={(next) => onPageChange(bucketKey, next)}
                />
              </TableCell>
            </TableRow>
          ) : null}
        </TableBody>
      ) : null}
    </>
  );
}

function GroupItemPager({
  label,
  page,
  pageSize,
  total,
  onPageChange,
}: {
  label: string;
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}): React.ReactElement {
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const end = Math.min(total, page * pageSize);
  const hasPrev = page > 1;
  const hasNext = end < total;
  return (
    <nav
      aria-label={`${label} records`}
      className="flex items-center justify-end gap-2 text-13 text-fg-muted"
    >
      <span className="tabular-nums">
        {start.toLocaleString()}-{end.toLocaleString()} /{" "}
        {total.toLocaleString()}
      </span>
      <Button
        type="button"
        variant="ghost"
        size="iconSm"
        aria-label={`Previous ${label} records`}
        disabled={!hasPrev}
        onClick={() => onPageChange(Math.max(1, page - 1))}
      >
        <ChevronLeft className="glyph" aria-hidden />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="iconSm"
        aria-label={`Next ${label} records`}
        disabled={!hasNext}
        onClick={() => onPageChange(page + 1)}
      >
        <ChevronRight className="glyph" aria-hidden />
      </Button>
    </nav>
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
  path: readonly string[];
  depth: number;
  rows: readonly TableRowModel<TRow>[];
  children: readonly RowGroup<TRow>[];
};

function groupRows<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  groupStack: readonly DataViewGroup[],
  depth = 0,
  parentPath: readonly string[] = [],
): readonly RowGroup<TRow>[] {
  const [group, ...rest] = groupStack;
  if (!group) {
    return [{
      key: groupPathKey(parentPath) || "root",
      label: null,
      path: parentPath,
      depth,
      rows,
      children: [],
    }];
  }
  const groups = new Map<string, TableRowModel<TRow>[]>();
  for (const row of rows) {
    const key = groupKey(readPath(row.original, group.field), group);
    const next = groups.get(key) ?? [];
    next.push(row);
    groups.set(key, next);
  }
  return [...groups.entries()].map(([label, groupRows]) => {
    const path = [...parentPath, label];
    return {
      key: groupPathKey(path),
      label,
      path,
      depth,
      rows: groupRows,
      children: groupRows.length > 0
        ? groupRowsByRest(groupRows, rest, depth + 1, path)
        : [],
    };
  });
}

function groupRowsByRest<TRow extends Row>(
  rows: readonly TableRowModel<TRow>[],
  groupStack: readonly DataViewGroup[],
  depth: number,
  parentPath: readonly string[],
): readonly RowGroup<TRow>[] {
  return groupRows(rows, groupStack, depth, parentPath).filter(
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

function dataViewGroupToAggregateDimension(
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

function aggregateKeyField(group: DataViewGroup): string {
  return group.granularity
    ? `${group.field}${titleCase(group.granularity).replace(/\s+/g, "")}`
    : group.field;
}

function buildGroupCountMap(
  buckets: readonly AggregateBucket[],
  groupStack: readonly DataViewGroup[],
): ReadonlyMap<string, number> {
  const counts = new Map<string, number>();
  if (groupStack.length === 0) return counts;
  for (const bucket of buckets) {
    const labels: string[] = [];
    for (const label of bucketValueLabels(bucket, groupStack)) {
      labels.push(label);
      const key = groupPathKey(labels);
      counts.set(key, (counts.get(key) ?? 0) + bucket.count);
    }
  }
  return counts;
}

function groupPathKey(path: readonly string[]): string {
  return JSON.stringify(path);
}

function stableBucketKey(bucket: AggregateBucket): string {
  return stableSerialize(bucket.key ?? null);
}

function bucketLabel(
  bucket: AggregateBucket,
  groupStack: readonly DataViewGroup[],
): string {
  const labels = bucketValueLabels(bucket, groupStack);
  if (labels.length === 0) return "All records";
  if (labels.length === 1) return labels[0] ?? "All records";
  return labels
    .map((label, index) => {
      const group = groupStack[index];
      return group ? `${groupFieldLabel(group.field)}: ${label}` : label;
    })
    .join(" / ");
}

function bucketValueLabels(
  bucket: AggregateBucket,
  groupStack: readonly DataViewGroup[],
): string[] {
  return groupStack.map((group) => {
    const value = bucket.key?.[aggregateKeyField(group)];
    return groupKey(value, group);
  });
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

function graphQLEnumValue(field: string): string {
  return field
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/[-\s]+/g, "_")
    .toUpperCase();
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

function renderCell<TRow extends Row>(
  cell: TableCellModel<TRow, unknown>,
): React.ReactNode {
  return flexRender(cell.column.columnDef.cell, cell.getContext());
}

function tableColumnLabel<TRow extends Row>(
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
  return columnMeta(column).align ?? "left";
}

function columnMeta<TRow extends Row>(
  column: ColumnDef<TRow>,
): { align?: PageColumnAlign; label?: React.ReactNode } {
  return (
    column.meta as
      | { align?: PageColumnAlign; label?: React.ReactNode }
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

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
  type HeaderGroup,
  type SortingState,
  type Table as TableModel,
} from "@tanstack/react-table";
import { type Virtualizer } from "@tanstack/react-virtual";
import {
  type AggregateBucket,
} from "@angee/refine";
import { Glyph } from "../../chrome/Glyph";
import { useUiT, type UiTranslate } from "../../i18n";
import { cn } from "../../lib/cn";
import type { DndPayload } from "../../lib/dnd";
import { ListStatusMessage } from "../../fragments/ListStatusMessage";
import { CountBadge } from "../../ui/count-badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../../ui/table";
import { textRoleVariants } from "../../ui/text";
import { depthIndent } from "../../ui/tree";
import type { ResourceViewContextValue } from "./resource-view-context";
import { useSelectionPlaces, type SelectionPlaces, type ShownSelection } from "./selection-channel";
import { placedRecords } from "./resource-view-grouped-model";
import type { ListViewNavigationScope, ResourceListSnapshot } from "./resource-view-surface";
import { EMPTY_ARRAY } from "./surface/types";
import {
  ALIGN_CLASS,
  ListEmpty,
  ListHeaderCell,
  ListLoadingFooter,
  RowActionsHeader,
  LeadingSelectionCell,
  ListSkeletonRows,
  MeasureFooter,
  RecordRow,
  SelectionToggle,
  VirtualPaddingRow,
  alignOf,
  formatMeasure,
  measureValue,
  useVirtualWindow,
  type GroupedListItem,
  type GroupedRecordNav,
  type GroupMeasure,
  type VisibleFieldOption,
} from "./resource-view-list-body";
import type {
  GroupLabelContext,
  ListEmptyContent,
  ResourceTableHeaderVisibility,
  ResourceTableLayout,
} from "./resource-view-types";

import { GroupedScopePager } from "./GroupedScopePager";
import { snapshotFromNav } from "./grouped-navigation";

export interface GroupedListBodyProps<TRow extends Row> {
  tableLayout?: ResourceTableLayout;
  headerVisibility?: ResourceTableHeaderVisibility;
  selectable?: boolean;
  renderGroupLabel?: (group: GroupLabelContext) => React.ReactNode;
  table: TableModel<TRow>;
  tableColumns: readonly ColumnDef<TRow>[];
  visibleColumnCount: number;
  visibleFields?: readonly VisibleFieldOption[];
  onVisibleFieldToggle?: (id: string, visible: boolean) => void;
  resourceView: ResourceViewContextValue;
  measures: readonly GroupMeasure[];
  listItems: readonly GroupedListItem<TRow>[];
  tableScrollRef: React.RefObject<HTMLDivElement | null>;
  rowVirtualizer: Virtualizer<HTMLDivElement, Element>;
  footerAggregate: AggregateBucket | null;
  expandedKeys: ReadonlySet<string>;
  toggleGroup: (key: string) => void;
  setGroupsExpanded: (keys: readonly string[], expanded: boolean) => void;
  setScopePage: (key: string, page: number) => void;
  setScopePageSize: (key: string, pageSize: number) => void;
  selectedIds: ReadonlySet<string>;
  /**
   * Present while "Select all N" is on: what the list shows selected, every loaded row,
   * which a write starts from. The header and every group's checkbox show checked.
   */
  shown?: ShownSelection;
  interactive: boolean;
  rowHref?: (row: TRow, scope?: ListViewNavigationScope) => string;
  renderRowActions?: (row: TRow) => React.ReactNode;
  onRowClick?: (row: TRow) => void;
  draggableRow?: (row: TRow) => DndPayload | null;
  onListStateChange?: (state: ResourceListSnapshot<TRow>) => void;
  emptyContent: ListEmptyContent;
  fetching: boolean;
  error: Error | null;
}

export function GroupedListBody<TRow extends Row>({
  tableLayout = "auto",
  headerVisibility = "visible",
  selectable = true,
  renderGroupLabel,
  table,
  visibleColumnCount,
  visibleFields = EMPTY_ARRAY,
  onVisibleFieldToggle,
  resourceView,
  measures,
  listItems,
  tableScrollRef,
  rowVirtualizer,
  footerAggregate,
  toggleGroup,
  setGroupsExpanded,
  setScopePage,
  setScopePageSize,
  selectedIds,
  shown,
  interactive,
  rowHref,
  renderRowActions,
  onRowClick,
  draggableRow,
  onListStateChange,
  emptyContent,
  fetching,
  error,
}: GroupedListBodyProps<TRow>): React.ReactElement {
  const t = useUiT();
  // Grouped mode keeps a sticky chevron column in place of the select-all box.
  const hasRowActions = renderRowActions !== undefined;
  const colSpan = Math.max(1, visibleColumnCount + 1 + (hasRowActions ? 1 : 0));
  const measuresByColumn = React.useMemo(
    () => new Map(measures.map((measure) => [measure.columnId, measure])),
    [measures],
  );
  const visibleColumns = table.getVisibleLeafColumns();
  const { paddingTop, paddingBottom, visibleIndexes } = useVirtualWindow(rowVirtualizer);
  const handleRecordOpen = React.useCallback(
    (nav: GroupedRecordNav) => onListStateChange?.(snapshotFromNav<TRow>(nav)),
    [onListStateChange],
  );
  const groupHeaders = React.useMemo(
    () => listItems.filter((item): item is GroupedHeaderItem<TRow> => item.kind === "groupHeader" && item.expandable),
    [listItems],
  );
  // The loaded records of each group (nested groups included), read from their own
  // group paths, and of the whole list.
  const { groupRecords, loadedRecords } = React.useMemo(() => {
    const byGroup = new Map<string, GroupedRecordItem<TRow>[]>();
    const all: GroupedRecordItem<TRow>[] = [];
    for (const item of listItems) {
      if (item.kind !== "record") continue;
      all.push(item);
      for (const { bucketKey } of item.place.path) {
        const records = byGroup.get(bucketKey);
        if (records) records.push(item);
        else byGroup.set(bucketKey, [item]);
      }
    }
    return { groupRecords: byGroup, loadedRecords: all };
  }, [listItems]);
  const { setRowSelection, toggleSelectedId } = useSelectionPlaces(shown);
  const allMatching = shown !== undefined;
  // Ticking captures where each record is shown in these records, again for one
  // already selected; one shown under several group paths captures none.
  const selectRecords = React.useCallback((records: readonly GroupedRecordItem<TRow>[], selected: boolean) => {
    const placed = placedRecords(records);
    setRowSelection((current) => {
      const next = { ...current };
      for (const { row } of records) {
        if (selected) next[row.id] = true;
        else delete next[row.id];
      }
      return next;
    }, (id) => table.getCoreRowModel().rowsById[id]?.original, (id) => placed.get(id) ?? undefined);
  }, [setRowSelection, table]);
  const selectedCount = loadedRecords.filter(({ row }) => selectedIds.has(row.id)).length;

  return (
    <>
      <div
        ref={tableScrollRef}
        className="resource-table-scroll min-h-0 min-w-0 overflow-auto overscroll-contain"
      >
        <Table className={tableLayout === "fixed" ? "table-fixed" : undefined}>
          <colgroup>
            <col className={selectable ? "w-14" : "w-8"} />
            {visibleColumns.map((column) => (
              <col key={column.id} />
            ))}
            {hasRowActions ? <col /> : null}
          </colgroup>
          <GroupedListHeader
            headerVisibility={headerVisibility}
            headerGroups={table.getHeaderGroups()}
            sorting={table.getState().sorting}
            selectable={selectable}
            expandableGroups={groupHeaders}
            onSetGroupsExpanded={setGroupsExpanded}
            allMatching={allMatching}
            loadedRecords={loadedRecords}
            selectedCount={selectedCount}
            onSelectRecords={selectRecords}
            resourceView={resourceView}
            visibleFields={visibleFields}
            onVisibleFieldToggle={onVisibleFieldToggle}
            trailingColumn={hasRowActions}
          />
          <TableBody>
            {fetching && listItems.length === 0 ? (
              // The grouped colgroup/header always reserve the leading chevron
              // column, so the first-page skeleton keeps its leading cell too
              // (default `selectable`) regardless of the list's selection mode —
              // matching the per-group leaf skeleton below.
              <ListSkeletonRows
                table={table}
                trailingColumn={hasRowActions}
                loadingLabel={t("list.loading")}
              />
            ) : error && listItems.length === 0 ? (
              <TableRow>
                <TableCell colSpan={colSpan} className="py-6 text-danger-text">
                  {error.message}
                </TableCell>
              </TableRow>
            ) : listItems.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={colSpan}
                  className="py-8 text-center text-fg-muted"
                >
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
                      renderGroupLabel={renderGroupLabel}
                      key={groupedItemKey(item)}
                      item={item}
                      colSpan={colSpan}
                      table={table}
                      visibleColumns={visibleColumns}
                      measuresByColumn={measuresByColumn}
                      selectable={selectable}
                      interactive={interactive}
                      rowHref={rowHref}
                      renderRowActions={renderRowActions}
                      onRowClick={onRowClick}
                      draggableRow={draggableRow}
                      onRecordOpen={handleRecordOpen}
                      onToggleGroup={toggleGroup}
                      groupRecords={groupRecords}
                      selectedIds={selectedIds}
                      allMatching={allMatching}
                      toggleSelectedId={toggleSelectedId}
                      onSelectRecords={selectRecords}
                      onPageChange={setScopePage}
                      onPageSizeChange={setScopePageSize}
                      loadingLabel={t("list.loading")}
                      t={t}
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
              measures={measures}
              aggregate={footerAggregate}
              selectable={selectable}
              // Grouped colgroup reserves the leading column on every row; keep the
              // footer's leading cell (and its total label) even when not selectable.
              reserveLeadingColumn
              labelInSelectionColumn
              trailingColumn={hasRowActions}
            />
          ) : null}
        </Table>
      </div>
      {fetching && listItems.length > 0 ? <ListLoadingFooter /> : null}
    </>
  );
}

interface GroupedListHeaderProps<TRow extends Row> {
  headerVisibility: ResourceTableHeaderVisibility;
  /** `table.getHeaderGroups()`, which the table keeps until a column is shown, hidden, moved or pinned. */
  headerGroups: readonly HeaderGroup<TRow>[];
  /** The table's sorting, which each header's sort button and `aria-sort` show through its column. */
  sorting: SortingState;
  selectable: boolean;
  /** The groups that open and close, which the header's chevron opens or closes together. */
  expandableGroups: readonly GroupedHeaderItem<TRow>[];
  onSetGroupsExpanded: (keys: readonly string[], expanded: boolean) => void;
  allMatching: boolean;
  /** Every loaded record, which the header's checkbox ticks, and how many of them show selected. */
  loadedRecords: readonly GroupedRecordItem<TRow>[];
  selectedCount: number;
  onSelectRecords: GroupedItemRowProps<TRow>["onSelectRecords"];
  resourceView: ResourceViewContextValue;
  visibleFields: readonly VisibleFieldOption[];
  onVisibleFieldToggle?: (id: string, visible: boolean) => void;
  trailingColumn: boolean;
}

function GroupedListHeaderInner<TRow extends Row>({
  headerVisibility,
  headerGroups,
  selectable,
  expandableGroups,
  onSetGroupsExpanded,
  allMatching,
  loadedRecords,
  selectedCount,
  onSelectRecords,
  resourceView,
  visibleFields,
  onVisibleFieldToggle,
  trailingColumn,
}: GroupedListHeaderProps<TRow>): React.ReactElement {
  const t = useUiT();
  const anyExpanded = expandableGroups.some((item) => item.expanded);
  const expandAll = expandableGroups.length > 0 ? (
    <button
      type="button"
      className="flex size-6 items-center justify-center rounded-4 text-fg-muted outline-none hover:bg-inset hover:text-fg focus-visible:focus-ring"
      aria-label={t(anyExpanded ? "list.collapseAllGroups" : "list.expandAllGroups")}
      title={t(anyExpanded ? "list.collapseAllGroups" : "list.expandAllGroups")}
      onClick={() => onSetGroupsExpanded(
        expandableGroups.filter((item) => item.expanded === anyExpanded).map((item) => item.bucketKey),
        !anyExpanded,
      )}
    >
      <Glyph name={anyExpanded ? "chevron-down" : "chevron-right"} className="size-3.5" />
    </button>
  ) : null;
  return (
    <TableHeader className={headerVisibility === "visually-hidden" ? "sr-only" : undefined}>
      {headerGroups.map((group) => (
        <TableRow key={group.id}>
          {selectable ? (
            <LeadingSelectionCell
              head
              grouped
              chevron={expandAll}
              label={t("list.selectAllLoaded")}
              // Under "Select all N" it shows checked even with every group collapsed, and unticking it leaves the mode.
              checked={allMatching || (loadedRecords.length > 0 && selectedCount === loadedRecords.length)}
              indeterminate={!allMatching && selectedCount > 0 && selectedCount < loadedRecords.length}
              disabled={!allMatching && loadedRecords.length === 0}
              onCheckedChange={(checked) => onSelectRecords(loadedRecords, checked)}
            />
          ) : (
            <TableHead sticky className="w-8 p-0">
              <div className="flex justify-center">{expandAll}</div>
            </TableHead>
          )}
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
          {trailingColumn ? <RowActionsHeader /> : null}
        </TableRow>
      ))}
    </TableHeader>
  );
}

// Memoised, as the rows are: the table keeps its header groups and sorting, the surface's
// items keep the groups and loaded records, and the callbacks are stable, so a range
// change leaves the header alone.
const GroupedListHeader = React.memo(GroupedListHeaderInner) as typeof GroupedListHeaderInner;

function groupedItemKey<TRow extends Row>(item: GroupedListItem<TRow>): string {
  switch (item.kind) {
    case "groupHeader":
      return `header:${item.bucketKey}`;
    case "record":
      return item.itemKey;
    case "skeleton":
    case "status":
      return item.itemKey;
  }
}

interface GroupedItemRowProps<TRow extends Row> {
  renderGroupLabel?: (group: GroupLabelContext) => React.ReactNode;
  item: GroupedListItem<TRow>;
  colSpan: number;
  table: TableModel<TRow>;
  visibleColumns: readonly TableColumn<TRow, unknown>[];
  measuresByColumn: ReadonlyMap<string, GroupMeasure>;
  selectable: boolean;
  interactive: boolean;
  rowHref?: (row: TRow, scope?: ListViewNavigationScope) => string;
  renderRowActions?: (row: TRow) => React.ReactNode;
  onRowClick?: (row: TRow) => void;
  draggableRow?: (row: TRow) => DndPayload | null;
  onRecordOpen: (nav: GroupedRecordNav) => void;
  onToggleGroup: (key: string) => void;
  groupRecords: ReadonlyMap<string, readonly GroupedRecordItem<TRow>[]>;
  /** What shows selected: the selection, and every loaded row under "Select all N". */
  selectedIds: ReadonlySet<string>;
  /** "Select all N" is on: every group shows selected, a collapsed one too. */
  allMatching: boolean;
  toggleSelectedId: SelectionPlaces["toggleSelectedId"];
  onSelectRecords: (records: readonly GroupedRecordItem<TRow>[], selected: boolean) => void;
  onPageChange: (key: string, page: number) => void;
  onPageSizeChange: (key: string, pageSize: number) => void;
  loadingLabel: React.ReactNode;
  t: UiTranslate;
}

function GroupedItemRowInner<TRow extends Row>({
  renderGroupLabel,
  item,
  colSpan,
  table,
  visibleColumns,
  measuresByColumn,
  selectable,
  interactive,
  rowHref,
  renderRowActions,
  onRowClick,
  draggableRow,
  onRecordOpen,
  onToggleGroup,
  groupRecords,
  selectedIds,
  allMatching,
  toggleSelectedId,
  onSelectRecords,
  onPageChange,
  onPageSizeChange,
  loadingLabel,
  t,
}: GroupedItemRowProps<TRow>): React.ReactElement {
  switch (item.kind) {
    case "groupHeader": {
      const records = groupRecords.get(item.bucketKey) ?? NO_RECORDS;
      return (
        <GroupedHeaderRow
          renderGroupLabel={renderGroupLabel}
          item={item}
          selectable={selectable}
          records={records}
          selectedCount={records.filter(({ row }) => selectedIds.has(row.id)).length}
          allMatching={allMatching}
          onSelectRecords={onSelectRecords}
          visibleColumns={visibleColumns}
          measuresByColumn={measuresByColumn}
          onToggle={onToggleGroup}
          trailingColumn={renderRowActions !== undefined}
          unavailableLabel={item.unavailableMessage ?? t("list.itemsUnavailable")}
          onPageChange={onPageChange}
          onPageSizeChange={onPageSizeChange}
          t={t}
        />
      );
    }
    case "record":
      return (
        <GroupedRecordRow
          item={item}
          selected={selectedIds.has(item.row.id)}
          toggleSelectedId={toggleSelectedId}
          selectable={selectable}
          interactive={interactive}
          rowHref={rowHref}
          onRowClick={onRowClick}
          draggableRow={draggableRow}
          onRecordOpen={onRecordOpen}
          renderRowActions={renderRowActions}
        />
      );
    case "skeleton":
      return (
        <ListSkeletonRows
          table={table}
          rowCount={item.rowCount}
          trailingColumn={renderRowActions !== undefined}
          loadingLabel={loadingLabel}
        />
      );
    case "status":
      return <GroupedStatusRow item={item} colSpan={colSpan} />;
  }
}

// Memoised, as `RecordRow` is: the surface's items and the body's callbacks are stable,
// so a range change renders only the items that enter the window.
const GroupedItemRow = React.memo(GroupedItemRowInner) as typeof GroupedItemRowInner;

type GroupedRecordItem<TRow extends Row> = Extract<GroupedListItem<TRow>, { kind: "record" }>;
type GroupedHeaderItem<TRow extends Row> = Extract<GroupedListItem<TRow>, { kind: "groupHeader" }>;
const NO_RECORDS: readonly never[] = [];

/** A record row whose callbacks stay stable across selection toggles, so the memoised row skips them. */
function GroupedRecordRow<TRow extends Row>({
  item,
  selected,
  toggleSelectedId,
  selectable,
  interactive,
  rowHref,
  onRowClick,
  draggableRow,
  onRecordOpen,
  renderRowActions,
}: Pick<GroupedItemRowProps<TRow>, "toggleSelectedId" | "selectable" | "interactive" | "rowHref" | "onRowClick"
  | "draggableRow" | "onRecordOpen" | "renderRowActions"> & { item: GroupedRecordItem<TRow>; selected: boolean }): React.ReactElement {
  const { nav, place } = item;
  // The row's selection captures where it is shown.
  const toggleSelected = React.useCallback((id: string, selected?: boolean, row?: TRow) =>
    toggleSelectedId(id, selected, row, place), [toggleSelectedId, place]);
  const href = React.useMemo(() => rowHref ? (row: TRow) => rowHref(row, nav) : undefined, [rowHref, nav]);
  const open = React.useCallback(() => onRecordOpen(nav), [onRecordOpen, nav]);
  return (
    <RecordRow
      row={item.row}
      cells={item.row.getVisibleCells()}
      selected={selected}
      onToggleSelected={toggleSelected}
      selectable={selectable}
      // The grouped colgroup/header reserve a leading chevron column on every
      // row, so a record row must keep it even when selection is off — else its
      // cells slide left into the 32px column under `table-fixed`.
      reserveLeadingColumn
      interactive={interactive}
      rowHref={href}
      onRowClick={onRowClick}
      draggableRow={draggableRow}
      onRecordOpen={open}
      renderRowActions={renderRowActions}
    />
  );
}

interface GroupedHeaderRowProps<TRow extends Row> {
  renderGroupLabel?: (group: GroupLabelContext) => React.ReactNode;
  item: GroupedHeaderItem<TRow>;
  /** The group has a checkbox, which ticks its loaded records. */
  selectable: boolean;
  /** The group's loaded records, nested groups' included. */
  records: readonly GroupedRecordItem<TRow>[];
  /** How many of them show selected. */
  selectedCount: number;
  /** "Select all N" is on: the group shows selected, a collapsed one too. */
  allMatching: boolean;
  onSelectRecords: GroupedItemRowProps<TRow>["onSelectRecords"];
  visibleColumns: readonly TableColumn<TRow, unknown>[];
  measuresByColumn: ReadonlyMap<string, GroupMeasure>;
  onToggle: (key: string) => void;
  trailingColumn: boolean;
  unavailableLabel: string;
  onPageChange: (key: string, page: number) => void;
  onPageSizeChange: (key: string, pageSize: number) => void;
  t: UiTranslate;
}

function GroupedHeaderRowInner<TRow extends Row>({
  renderGroupLabel,
  item,
  selectable,
  records,
  selectedCount,
  allMatching,
  onSelectRecords,
  visibleColumns,
  measuresByColumn,
  onToggle,
  trailingColumn,
  unavailableLabel,
  onPageChange,
  onPageSizeChange,
  t,
}: GroupedHeaderRowProps<TRow>): React.ReactElement {
  const { bucket, bucketKey, depth, label, count, expandable, expanded } = item;
  const selection: React.ComponentProps<typeof SelectionToggle> | undefined = selectable ? {
    label: t("list.selectGroup", { label }),
    checked: allMatching || (records.length > 0 && selectedCount === records.length),
    indeterminate: !allMatching && selectedCount > 0 && selectedCount < records.length,
    // Only loaded rows can be selected or unselected, so a group with none loaded
    // (collapsed, or its page loading) cannot change, under "Select all N" too, where
    // it shows selected; a collapsed one says why.
    disabled: records.length === 0,
    disabledReason: expandable && !expanded ? t("list.expandGroupToChangeSelection") : null,
    onCheckedChange: (checked: boolean) => onSelectRecords(records, checked),
  } : undefined;
  // Keep aggregate cells numeric; chrome belongs in the last ordinary column
  // (or the existing action column), not in a measure's accessible value.
  const ordinaryColumns = visibleColumns.filter(
    (column) => !measuresByColumn.has(column.id),
  );
  const labelColumn = ordinaryColumns[0]?.id;
  const pagerColumn = ordinaryColumns.at(-1)?.id;
  const labelContent = (
    <span className="inline-flex min-w-0 max-w-full items-center gap-2">
      <span className="min-w-0 truncate">
        {renderGroupLabel ? renderGroupLabel({ bucket, label, depth }) : label}
      </span>
      <CountBadge value={count} />
      {!expandable ? (
        <span className={cn(textRoleVariants({ role: "meta" }), "font-normal")}>
          {unavailableLabel}
        </span>
      ) : null}
    </span>
  );
  const pager = item.pager ? (
    <GroupedScopePager
      pager={item.pager}
      label={label}
      onPageChange={onPageChange}
      onPageSizeChange={onPageSizeChange}
      t={t}
    />
  ) : null;
  const toggle = (): void => {
    if (expandable) onToggle(bucketKey);
  };
  return (
    <TableRow
      className={expandable && !renderGroupLabel ? "cursor-pointer" : undefined}
      tabIndex={expandable && !renderGroupLabel ? 0 : undefined}
      aria-expanded={expandable ? expanded : false}
      onClick={renderGroupLabel ? undefined : toggle}
      onKeyDown={(event) => {
        if (
          !renderGroupLabel &&
          event.target === event.currentTarget &&
          (event.key === "Enter" || event.key === " ")
        ) {
          event.preventDefault();
          toggle();
        }
      }}
    >
      <TableCell className={cn("h-9 bg-sheet-2 p-0", selection ? "w-14" : "w-8")}>
        <div className="flex min-h-9 items-center gap-2">
          <button
            type="button"
            className={cn(
              "flex min-h-9 w-8 shrink-0 items-center justify-center px-2 text-left text-13 outline-none",
              "focus-visible:focus-ring",
              expandable
                ? "text-fg hover:bg-inset"
                : "cursor-not-allowed text-fg-muted",
            )}
            aria-label={label}
            aria-expanded={expandable ? expanded : false}
            aria-disabled={!expandable}
            onClick={(event) => {
              event.stopPropagation();
              toggle();
            }}
          >
            <Glyph
              name={expanded && expandable ? "chevron-down" : "chevron-right"}
              className="size-3.5 shrink-0 text-fg-muted"
            />
          </button>
          {/* Sits flush against the chevron (cancelling the row gap) so it lines up with the header's and the rows' checkboxes. */}
          {selection ? <SelectionToggle {...selection} className="-ml-2 min-h-9 w-6 shrink-0" /> : null}
          {!labelColumn ? labelContent : null}
          {!trailingColumn && !pagerColumn ? pager : null}
        </div>
      </TableCell>
      {visibleColumns.map((column) => {
        const measure = measuresByColumn.get(column.id);
        const value = measure ? measureValue(bucket, measure) : undefined;
        const formatted =
          measure && value != null ? formatMeasure(value, measure) : "";
        return (
          <TableCell
            key={column.id}
            className={cn(
              "h-9 bg-sheet-2 text-13",
              ALIGN_CLASS[alignOf(column.columnDef)],
              column.id === labelColumn ? "font-semibold" : "",
            )}
            style={
              column.id === labelColumn && depth > 0 ? { paddingLeft: depthIndent(depth) } : undefined
            }
            aria-label={
              measure
                ? `${label} ${measure.label}${formatted ? `: ${formatted}` : ""}`
                : undefined
            }
          >
            <div className="flex min-w-0 items-center gap-3">
              <div className="min-w-0 flex-1">
                {measure
                  ? formatted
                  : column.id === labelColumn
                    ? labelContent
                    : null}
              </div>
              {!trailingColumn && column.id === pagerColumn ? pager : null}
            </div>
          </TableCell>
        );
      })}
      {trailingColumn ? (
        <TableCell className="h-9 bg-sheet-2">{pager}</TableCell>
      ) : null}
    </TableRow>
  );
}

// Memoised: its group's records, their selected count and the callbacks are stable, so
// a selection change elsewhere in the list leaves the header alone.
const GroupedHeaderRow = React.memo(GroupedHeaderRowInner) as typeof GroupedHeaderRowInner;

function GroupedStatusRow<TRow extends Row>({
  item,
  colSpan,
}: {
  item: Extract<GroupedListItem<TRow>, { kind: "status" }>;
  colSpan: number;
}): React.ReactElement {
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="py-4" style={item.depth > 0 ? { paddingLeft: depthIndent(item.depth) } : undefined}>
        <ListStatusMessage tone={item.tone} align={item.tone === "danger" ? "start" : "center"}>{item.message}</ListStatusMessage>
      </TableCell>
    </TableRow>
  );
}

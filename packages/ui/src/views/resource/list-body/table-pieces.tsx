import * as React from "react";
import {
  flexRender,
  type Header as TableHeaderModel,
  type HeaderGroup,
  type SortingState,
  type Table as TableModel,
  type Row as TableRowModel,
} from "@tanstack/react-table";
import type { Virtualizer } from "@tanstack/react-virtual";
import type { AggregateBucket } from "@angee/refine";
import type { Row } from "@angee/metadata";
import { Glyph } from "../../../chrome/Glyph";
import { useDeveloperFieldTitle } from "../../../chrome/DeveloperMode";
import { useUiT } from "../../../i18n";
import { type DndPayload } from "../../../lib/dnd";
import { CheckboxVisual } from "../../../ui/checkbox";
import { DropdownMenu } from "../../../ui/dropdown-menu";
import { ActionMenu } from "../../../toolbars/ActionMenu";
import { SelectionBar as SelectionBarPrimitive } from "../../../ui/selection-bar";
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "../../../ui/table";
import type { ResourceViewContextValue } from "../resource-view-context";
import type { ResourceViewGroup } from "../resource-view-model";
import type { ListEmptyContent, ResourceTableHeaderVisibility, ResourceTableLayout } from "../resource-view-types";
import type { ColumnDescriptor } from "../../page";
import { alignOf, formatMeasure, groupMeasuresFromColumns, headerAriaSort, measureValue } from "./cell-utils";
import { ListEmpty, ListSkeletonRows } from "./loading";
import { LeadingSelectionCell, renderListRow } from "./rows";
import { ALIGN_CLASS } from "./types";
import type { GroupMeasure, VisibleFieldOption } from "./types";
import { useVirtualWindow, VirtualPaddingRow } from "./virtualization";
import { useRuntimeViewAs } from "../../../runtime";
import { EMPTY_ARRAY, type ResourceListResult } from "../surface/types";
/** "Select all N" in the list's bar (`ListViewProps.selectAllMatching`). */
export interface ListSelectionMatching {
  /** The records or groups the list's read matches, as its pager counts them. */
  total: number;
  unit: ResourceListResult["pageUnit"];
  /** The selection is all of them: the bar reads "All N selected". */
  selected: boolean;
  /** Offers "Select all N"; absent when it is not offered. */
  onSelectAll?: () => void;
}

/** The list's bulk-selection band over the shared `SelectionBar`, flush with the table top.
 * @owner SelectionBar
 */
export function ListSelectionBar({
  count,
  onClear,
  onDelete,
  deletePending = false,
  deleteDisabledReason,
  pending = false,
  actions: extraActions,
  matching,
}: {
  count: number;
  onClear: () => void;
  onDelete?: () => void;
  deletePending?: boolean;
  /** Why Delete must not run (`Button.disabledReason`); null is none yet, keeping its tree for one to come. */
  deleteDisabledReason?: React.ReactNode;
  pending?: boolean;
  /** Caller-supplied bulk actions rendered before the built-in Delete/Clear. */
  actions?: React.ReactNode;
  matching?: ListSelectionMatching;
}): React.ReactElement {
  const t = useUiT();
  const preview = useRuntimeViewAs();
  const previewBlocked = Boolean(preview.viewAs || preview.pending);
  const groups = matching?.unit === "groups";
  // Choosing "Select all N" unmounts it: focus stays in the bar, on Clear.
  const clear = React.useRef<HTMLElement>(null);
  const actions = (
    <>
      {matching?.onSelectAll ? (
        <SelectionBarPrimitive.Action surface="brand" disabled={pending}
          onClick={() => { matching.onSelectAll?.(); clear.current?.focus(); }}>
          {t(groups ? "list.selectAllMatchingGroups" : "list.selectAllMatchingRecords", { count: matching.total })}
        </SelectionBarPrimitive.Action>
      ) : null}
      {extraActions}
      {onDelete ? (
        <SelectionBarPrimitive.Action
          surface="brand"
          tone="danger"
          pending={deletePending}
          disabled={previewBlocked || pending}
          {...(deleteDisabledReason === undefined ? {} : { disabledReason: deleteDisabledReason })}
          onClick={() => { if (!previewBlocked) onDelete(); }}
        >
          <Glyph name="trash" />
          {t("selection.delete")}
        </SelectionBarPrimitive.Action>
      ) : null}
      <SelectionBarPrimitive.Action ref={clear} surface="brand" disabled={pending} onClick={onClear}>
        {t("selection.clear")}
      </SelectionBarPrimitive.Action>
    </>
  );
  return (
    <SelectionBarPrimitive
      className="min-h-11 w-full rounded-none border-b border-border-subtle shadow-none"
      count={count}
      countLabel={matching?.selected
        ? t(groups ? "list.allMatchingGroupsSelected" : "list.allMatchingRecordsSelected", { count: matching.total })
        : t("selection.countSelected", { count })}
      actions={actions}
    />
  );
}

const NO_ROW_ACTIONS = (): null => null;

export interface FlatListBodyProps<TRow extends Row> {
  tableLayout?: ResourceTableLayout;
  headerVisibility?: ResourceTableHeaderVisibility;
  columns: readonly ColumnDescriptor<TRow>[];
  table: TableModel<TRow>;
  rowModels: readonly TableRowModel<TRow>[];
  tableScrollRef: React.RefObject<HTMLDivElement | null>;
  rowVirtualizer: Virtualizer<HTMLDivElement, Element>;
  visibleColumnCount: number;
  allPageSelected: boolean;
  somePageSelected: boolean;
  onPageSelectionChange: (checked: boolean) => void;
  visibleFields?: readonly VisibleFieldOption[];
  onVisibleFieldToggle?: (id: string, visible: boolean) => void;
  resourceView: ResourceViewContextValue;
  groupStack: readonly ResourceViewGroup[];
  interactive: boolean;
  selectable?: boolean;
  rowHref?: (row: TRow) => string;
  renderRowActions?: (row: TRow) => React.ReactNode;
  onRowClick?: (row: TRow) => void;
  activeRowId?: string | null;
  draggableRow?: (row: TRow) => DndPayload | null;
  onReorder?: (fromId: string, toId: string) => void;
  /** Whether a row may move; a row that may not shows a lock where its handle would be. */
  canReorderRow?: (row: TRow) => boolean;
  /** Content of one footer cell spanning the visible table columns. */
  footerRow?: React.ReactNode;
  emptyContent: ListEmptyContent;
  fetching: boolean;
  footerAggregate?: AggregateBucket | null;
}

export function FlatListBody<TRow extends Row>({
  tableLayout = "auto",
  headerVisibility = "visible",
  columns,
  table,
  rowModels,
  tableScrollRef,
  rowVirtualizer,
  visibleColumnCount,
  allPageSelected,
  somePageSelected,
  onPageSelectionChange,
  visibleFields = EMPTY_ARRAY,
  onVisibleFieldToggle,
  resourceView,
  groupStack,
  interactive,
  selectable = true,
  rowHref,
  renderRowActions,
  onRowClick,
  activeRowId,
  draggableRow,
  onReorder,
  canReorderRow,
  footerRow,
  emptyContent,
  fetching,
  footerAggregate,
}: FlatListBodyProps<TRow>): React.ReactElement {
  const t = useUiT();
  const hasRowActions = renderRowActions !== undefined;
  // The visible-fields menu sits in its own trailing header cell (shared with the row
  // actions when they exist), so every data column's header aligns with its values.
  const trailingColumn = hasRowActions || visibleFields.length > 0;
  // An empty trailing cell keeps rows aligned under the fields-menu header.
  const rowActions = renderRowActions ?? (trailingColumn ? NO_ROW_ACTIONS : undefined);
  const reorderType = `angee.list-row.${React.useId()}`;
  // Each record row's reorder, built once per row set, so a render that keeps the rows
  // (a scroll frame) hands every row the same one and its memo holds.
  const reorderById = React.useMemo(() => {
    if (!onReorder) return null;
    const rows = rowModels.filter((row) => !row.getIsGrouped());
    const move = (fromId: string, toId: string) => {
      const from = table.getCoreRowModel().rowsById[fromId];
      if (from && (canReorderRow?.(from.original) ?? true)) onReorder(fromId, toId);
    };
    return new Map(rows.map((row, index) => [row.id, {
      type: reorderType,
      onReorder: move,
      previousId: rows[index - 1]?.id,
      nextId: rows[index + 1]?.id,
      locked: canReorderRow ? !canReorderRow(row.original) : false,
    }]));
  }, [onReorder, rowModels, table, canReorderRow, reorderType]);
  const colSpan = Math.max(
    1,
    visibleColumnCount + (selectable ? 1 : 0) + (trailingColumn ? 1 : 0) + (onReorder ? 1 : 0),
  );
  const measures = React.useMemo(
    () => groupMeasuresFromColumns(columns),
    [columns],
  );
  const { paddingTop, paddingBottom, visibleIndexes } = useVirtualWindow(rowVirtualizer);
  const extraFooterRow = footerRow == null ? null : (
    <TableRow><TableCell colSpan={colSpan}>{footerRow}</TableCell></TableRow>
  );
  return (
    <div
      ref={tableScrollRef}
      className="resource-table-scroll min-h-0 min-w-0 overflow-auto overscroll-contain"
    >
      <Table className={tableLayout === "fixed" ? "table-fixed" : undefined}>
        <FlatListHeader
          headerVisibility={headerVisibility}
          headerGroups={table.getHeaderGroups()}
          sorting={table.getState().sorting}
          reorderable={Boolean(onReorder)}
          selectable={selectable}
          allPageSelected={allPageSelected}
          somePageSelected={somePageSelected}
          onPageSelectionChange={onPageSelectionChange}
          resourceView={resourceView}
          trailingColumn={trailingColumn}
          visibleFields={visibleFields}
          onVisibleFieldToggle={onVisibleFieldToggle}
        />
        <TableBody>
          {fetching && rowModels.length === 0 ? (
            <ListSkeletonRows
              table={table}
              selectable={selectable}
              reorderable={Boolean(onReorder)}
              trailingColumn={trailingColumn}
              loadingLabel={t("list.loading")}
            />
          ) : rowModels.length === 0 ? (
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
                <VirtualPaddingRow
                  height={paddingTop}
                  colSpan={colSpan}
                />
              ) : null}
              {visibleIndexes.map((index) => {
                const row = rowModels[index];
                return row
                  ? renderListRow({
                      row,
                      colSpan,
                      resourceView,
                      groupStack,
                      interactive,
                      selectable,
                      rowHref,
                      onRowClick,
                      activeRowId,
                      draggableRow,
                      renderRowActions: rowActions,
                      reorder: reorderById?.get(row.id),
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
        {measures.length > 0 && footerAggregate ? (
          <MeasureFooter
            table={table}
            measures={measures}
            aggregate={footerAggregate}
            selectable={selectable}
            trailingColumn={trailingColumn}
            reorderable={Boolean(onReorder)}
          >{extraFooterRow}</MeasureFooter>
        ) : extraFooterRow ? <TableFooter>{extraFooterRow}</TableFooter> : null}
      </Table>
    </div>
  );
}

interface FlatListHeaderProps<TRow extends Row> {
  headerVisibility: ResourceTableHeaderVisibility;
  /** `table.getHeaderGroups()`, which the table keeps until a column is shown, hidden, moved or pinned. */
  headerGroups: readonly HeaderGroup<TRow>[];
  /** The table's sorting: a change renders the memoised header again, and each header reads its sort from the table (`headerSort`). */
  sorting: SortingState;
  reorderable: boolean;
  selectable: boolean;
  allPageSelected: boolean;
  somePageSelected: boolean;
  onPageSelectionChange: (checked: boolean) => void;
  resourceView: ResourceViewContextValue;
  trailingColumn: boolean;
  visibleFields: readonly VisibleFieldOption[];
  onVisibleFieldToggle?: (id: string, visible: boolean) => void;
}

function FlatListHeaderInner<TRow extends Row>({
  headerVisibility,
  headerGroups,
  reorderable,
  selectable,
  allPageSelected,
  somePageSelected,
  onPageSelectionChange,
  resourceView,
  trailingColumn,
  visibleFields,
  onVisibleFieldToggle,
}: FlatListHeaderProps<TRow>): React.ReactElement {
  const t = useUiT();
  return (
    <TableHeader className={headerVisibility === "visually-hidden" ? "sr-only" : undefined}>
      {headerGroups.map((group) => (
        <TableRow key={group.id}>
          {reorderable ? <TableHead sticky className="w-8">
            <span className="sr-only">{t("list.reorderRow")}</span>
          </TableHead> : null}
          {selectable ? (
            <LeadingSelectionCell
              head
              label={t("list.selectAllOnPage")}
              checked={allPageSelected}
              indeterminate={!allPageSelected && somePageSelected}
              onCheckedChange={onPageSelectionChange}
            />
          ) : null}
          {group.headers.map((header) => (
            <ListHeaderCell
              key={header.id}
              header={header}
              resourceView={resourceView}
            />
          ))}
          {trailingColumn ? <RowActionsHeader>
            {visibleFields.length > 0 ? <VisibleFieldsMenu fields={visibleFields} onToggle={onVisibleFieldToggle} /> : null}
          </RowActionsHeader> : null}
        </TableRow>
      ))}
    </TableHeader>
  );
}

// Memoised, as the rows are: the table keeps its header groups and sorting, and the
// surface its selection callbacks and visible fields, so a range change leaves it alone.
const FlatListHeader = React.memo(FlatListHeaderInner) as typeof FlatListHeaderInner;

export function MeasureFooter<TRow extends Row>({
  table,
  measures,
  aggregate,
  selectable,
  reserveLeadingColumn = false,
  labelInSelectionColumn = false,
  trailingColumn = false,
  reorderable = false,
  children,
}: {
  table: TableModel<TRow>;
  measures: readonly GroupMeasure[];
  aggregate: AggregateBucket;
  selectable: boolean;
  /**
   * Emit the leading column cell even when not `selectable`. The grouped body's
   * `<colgroup>`/header reserve that column for the chevron on every row, so its
   * footer keeps it too (and hosts the `labelInSelectionColumn` total); the flat
   * body has no leading column unless selectable and leaves this unset.
   */
  reserveLeadingColumn?: boolean;
  labelInSelectionColumn?: boolean;
  trailingColumn?: boolean;
  reorderable?: boolean;
  children?: React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  const byColumn = new Map(measures.map((measure) => [measure.columnId, measure]));
  return (
    <TableFooter>
      <TableRow>
        {reorderable ? <TableCell className="w-8" /> : null}
        {selectable || reserveLeadingColumn ? (
          <TableCell className="w-8 text-fg-muted">
            {labelInSelectionColumn ? t("list.total") : null}
          </TableCell>
        ) : null}
        {table.getVisibleLeafColumns().map((column, index) => {
          const measure = byColumn.get(column.id);
          const value = measure ? measureValue(aggregate, measure) : undefined;
          const formatted = measure && value != null
            ? formatMeasure(value, measure)
            : "";
          return (
            <TableCell
              key={column.id}
              className={ALIGN_CLASS[alignOf(column.columnDef)]}
              aria-label={
                measure
                  ? formatted
                    ? t("list.totalMeasureValue", { label: measure.label, value: formatted })
                    : t("list.totalMeasure", { label: measure.label })
                  : undefined
              }
            >
              {measure ? (
                formatted
              ) : index === 0 ? (
                <span className="text-fg-muted">{t("list.total")}</span>
              ) : null}
            </TableCell>
          );
        })}
        {trailingColumn ? <TableCell /> : null}
      </TableRow>
      {children}
    </TableFooter>
  );
}

/** Accessible, non-sortable header for the framework-owned row-action column. */
export function RowActionsHeader({ children }: { children?: React.ReactNode } = {}): React.ReactElement {
  const t = useUiT();
  return (
    <TableHead sticky className="w-10 text-right">
      <span className="sr-only">{t("list.actions")}</span>
      {children}
    </TableHead>
  );
}

export function ListHeaderCell<TRow extends Row>({
  header,
  resourceView,
  visibleFields = [],
  onVisibleFieldToggle,
  withVisibleFields = false,
}: {
  header: TableHeaderModel<TRow, unknown>;
  resourceView: ResourceViewContextValue;
  visibleFields?: readonly VisibleFieldOption[];
  onVisibleFieldToggle?: (id: string, visible: boolean) => void;
  withVisibleFields?: boolean;
}): React.ReactElement {
  const content = header.isPlaceholder
    ? null
    : flexRender(header.column.columnDef.header, header.getContext());
  const showVisibleFields = withVisibleFields && visibleFields.length > 0;
  const developerTitle = useDeveloperFieldTitle();
  return (
    <TableHead
      sticky
      title={header.isPlaceholder ? undefined : developerTitle(header.column.id, resourceView.resource)}
      className={ALIGN_CLASS[alignOf(header.column.columnDef)]}
      style={{ minWidth: header.column.columnDef.minSize }}
      aria-sort={headerAriaSort(header.column, header.getContext().table)}
    >
      {showVisibleFields ? (
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
          <span className="min-w-0 truncate">{content}</span>
          <VisibleFieldsMenu
            fields={visibleFields}
            onToggle={onVisibleFieldToggle}
          />
        </div>
      ) : (
        content
      )}
    </TableHead>
  );
}

export function VisibleFieldsMenu({
  fields,
  onToggle,
}: {
  fields: readonly VisibleFieldOption[];
  onToggle?: (id: string, visible: boolean) => void;
}): React.ReactElement {
  const t = useUiT();
  return (
    <ActionMenu label={null} aria-label={t("list.visibleFields")} glyph="columns" size="iconSm" align="end" className="justify-self-end">
      <DropdownMenu.Group>
        <DropdownMenu.Label>{t("list.visibleFields")}</DropdownMenu.Label>
        {fields.map((field) => (
          <DropdownMenu.CheckboxItem
            key={field.id}
            inset={false}
            checked={field.visible}
            disabled={field.disabled}
            onCheckedChange={(checked) => {
              if (field.disabled && !checked) return;
              onToggle?.(field.id, checked);
            }}
          >
            <CheckboxVisual checked={field.visible} />
            <span className="min-w-0 truncate">{field.label}</span>
          </DropdownMenu.CheckboxItem>
        ))}
      </DropdownMenu.Group>
    </ActionMenu>
  );
}

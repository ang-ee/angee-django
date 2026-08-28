import * as React from "react";
import { flexRender, type Header as TableHeaderModel, type Table as TableModel, type Row as TableRowModel } from "@tanstack/react-table";
import type { Virtualizer } from "@tanstack/react-virtual";
import type { AggregateBucket } from "@angee/refine";
import type { Row } from "@angee/metadata";
import { Glyph } from "../../../chrome/Glyph";
import { useUiT } from "../../../i18n";
import { type DndPayload } from "../../../lib/dnd";
import { Button } from "../../../ui/button";
import { Checkbox, CheckboxVisual } from "../../../ui/checkbox";
import { DropdownMenu } from "../../../ui/dropdown-menu";
import { SelectionBar as SelectionBarPrimitive } from "../../../ui/selection-bar";
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "../../../ui/table";
import type { ResourceViewContextValue } from "../resource-view-context";
import type { ResourceViewGroup } from "../resource-view-model";
import type { ListEmptyContent } from "../resource-view-types";
import type { ColumnDescriptor } from "../../page";
import { alignOf, ariaSortForColumn, formatMeasure, groupMeasuresFromColumns, measureValue } from "./cell-utils";
import { ListEmpty, ListSkeletonRows } from "./loading";
import { renderListRow } from "./rows";
import { ALIGN_CLASS, GROUP_ROW_HEIGHT, RECORD_ROW_HEIGHT, TABLE_SCROLL_STYLE } from "./types";
import type { GroupMeasure, VisibleFieldOption } from "./types";
import { useVirtualWindow, VirtualPaddingRow } from "./virtualization";
export function SelectionBar({
  count,
  onClear,
  onDelete,
  deletePending = false,
  actions: extraActions,
}: {
  count: number;
  onClear: () => void;
  onDelete?: () => void;
  deletePending?: boolean;
  /** Caller-supplied bulk actions rendered before the built-in Delete/Clear. */
  actions?: React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  const actions = (
    <>
      {extraActions}
      {onDelete ? (
        <SelectionBarPrimitive.Action
          surface="brand"
          pending={deletePending}
          onClick={onDelete}
        >
          <Glyph name="trash" />
          {t("selection.delete")}
        </SelectionBarPrimitive.Action>
      ) : null}
      <SelectionBarPrimitive.Action surface="brand" onClick={onClear}>
        {t("selection.clear")}
      </SelectionBarPrimitive.Action>
    </>
  );
  return (
    <SelectionBarPrimitive
      className="h-11 w-full rounded-none border-b border-border-subtle shadow-none"
      count={count}
      countLabel={t("selection.countSelected", { count })}
      actions={actions}
    />
  );
}

export interface FlatListBodyProps<TRow extends Row> {
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
  emptyContent: ListEmptyContent;
  fetching: boolean;
  footerAggregate?: AggregateBucket | null;
}

export function FlatListBody<TRow extends Row>({
  columns,
  table,
  rowModels,
  tableScrollRef,
  rowVirtualizer,
  visibleColumnCount,
  allPageSelected,
  somePageSelected,
  onPageSelectionChange,
  visibleFields = [],
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
  emptyContent,
  fetching,
  footerAggregate,
}: FlatListBodyProps<TRow>): React.ReactElement {
  const t = useUiT();
  const hasRowActions = renderRowActions !== undefined;
  const colSpan = Math.max(
    1,
    visibleColumnCount + (selectable ? 1 : 0) + (hasRowActions ? 1 : 0),
  );
  const measures = React.useMemo(
    () => groupMeasuresFromColumns(columns),
    [columns],
  );
  const { paddingTop, paddingBottom, visibleIndexes } = useVirtualWindow(
    rowVirtualizer,
    rowModels.length,
    (index) =>
      rowModels[index]?.getIsGrouped() ? GROUP_ROW_HEIGHT : RECORD_ROW_HEIGHT,
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
              {selectable ? (
                <TableHead sticky className="w-8">
                  <Checkbox
                    size="sm"
                    aria-label={t("list.selectAllOnPage")}
                    checked={allPageSelected}
                    indeterminate={!allPageSelected && somePageSelected}
                    onCheckedChange={onPageSelectionChange}
                  />
                </TableHead>
              ) : null}
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
              {hasRowActions ? <RowActionsHeader /> : null}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {fetching && rowModels.length === 0 ? (
            <ListSkeletonRows
              table={table}
              selectable={selectable}
              trailingColumn={hasRowActions}
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
                      renderRowActions,
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
            trailingColumn={hasRowActions}
          />
        ) : null}
      </Table>
    </div>
  );
}

export function MeasureFooter<TRow extends Row>({
  table,
  measures,
  aggregate,
  selectable,
  labelInSelectionColumn = false,
  trailingColumn = false,
}: {
  table: TableModel<TRow>;
  measures: readonly GroupMeasure[];
  aggregate: AggregateBucket;
  selectable: boolean;
  labelInSelectionColumn?: boolean;
  trailingColumn?: boolean;
}): React.ReactElement {
  const t = useUiT();
  const byColumn = new Map(measures.map((measure) => [measure.columnId, measure]));
  return (
    <TableFooter>
      <TableRow>
        {selectable ? (
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
    </TableFooter>
  );
}

/** Accessible, non-sortable header for the framework-owned row-action column. */
export function RowActionsHeader(): React.ReactElement {
  const t = useUiT();
  return (
    <TableHead sticky className="text-right">
      <span className="sr-only">{t("list.actions")}</span>
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
  return (
    <TableHead
      sticky
      className={ALIGN_CLASS[alignOf(header.column.columnDef)]}
      aria-sort={ariaSortForColumn(header.column, resourceView)}
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
    <DropdownMenu.Root>
      <DropdownMenu.Trigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="iconSm"
            aria-label={t("list.visibleFields")}
            className="justify-self-end"
          >
            <Glyph name="columns" />
          </Button>
        }
      />
      <DropdownMenu.Portal>
        <DropdownMenu.Positioner sideOffset={6} align="end">
          <DropdownMenu.Content className="w-56">
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
          </DropdownMenu.Content>
        </DropdownMenu.Positioner>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

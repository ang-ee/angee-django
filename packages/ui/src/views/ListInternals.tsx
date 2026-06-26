// Leaf row/column/cell primitives for the resource-view list surfaces: record rows,
// `cellContent`, column building, and the group key/label helpers. Imports only
// ui/sdk/page leaves so parent view modules can depend on it without a cycle.
import * as React from "react";
import {
  Link,
  useNavigate } from "@tanstack/react-router";
import {
  flexRender,
  type Cell as TableCellModel,
  type Column as TableColumn,
  type ColumnDef,
  type Header as TableHeaderModel,
  type Table as TableModel,
  type Row as TableRowModel,
  } from "@tanstack/react-table";
import type { Virtualizer } from "@tanstack/react-virtual";
import type {
  AggregateBucket,
  AggregateMeasure,
  AggregateMeasureOperator,
  GroupDimension as HasuraGroupDimension,
  GroupOrder,
  } from "@angee/refine";
import type {
  DataResourceGroupBucketFilterMetadata,
  DataResourceGroupDimensionMetadata,
  DataResourceGroupExtractionMetadata,
  ModelEnumValueMetadata,
  ModelMetadata,
  Row,
} from "@angee/resources";
import { format } from "date-fns";
import { Spinner } from "../ui/spinner";

import { Glyph } from "../chrome/Glyph";
import { EmptyState } from "../fragments/EmptyState";
import { useBaseT } from "../i18n";
import { RelativeTime } from "../fragments/RelativeTime";
import { cn } from "../lib/cn";
import { dragSourceProps, type DndPayload, type DragSourceProps } from "../lib/dnd";
import { statusLabel } from "../lib/labels";
import { titleCase } from "../lib/titleCase";
import { Badge } from "../ui/badge";
import { Button, buttonVariants, type ButtonVariant } from "../ui/button";
import { Checkbox, CheckboxVisual } from "../ui/checkbox";
import { Chip } from "../ui/chip";
import { DropdownMenu } from "../ui/dropdown-menu";
import { SelectionBar as SelectionBarPrimitive } from "../ui/selection-bar";
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
import { useResolvedWidget } from "../widgets";
import { dateFromUnknown } from "../widgets/date-format";
import type { ResourceViewContextValue } from "./resource-view-context";
import type {
  ResourceViewFilter,
  ResourceViewGroup,
} from "./resource-view-model";
import type {
  ListEmptyAction,
  ListEmptyContent,
  ListEmptyState,
} from "./list-view-types";
import { columnTone } from "./page";
import type {
  ColumnAggregate,
  ColumnDescriptor,
  PageColumnAlign,
} from "./page";

export type ColumnAlign = PageColumnAlign;
export type ListColumn<TRow extends Row = Row> = ColumnDescriptor<TRow>;

export interface VisibleFieldOption {
  id: string;
  label: React.ReactNode;
  visible: boolean;
  disabled?: boolean;
}

export interface GroupByDimension {
  field: string;
  key?: string;
  granularity?: string;
  rangeKey?: string;
}

export type RowGroup<TRow extends Row> = {
  key: string;
  label: string | null;
  path: readonly string[];
  depth: number;
  rows: readonly TableRowModel<TRow>[];
  children: readonly RowGroup<TRow>[];
};

export interface GroupMeasure extends AggregateMeasure {
  field: string;
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
  actions: extraActions,
}: {
  count: number;
  onClear: () => void;
  onDelete?: () => void;
  deletePending?: boolean;
  /** Caller-supplied bulk actions rendered before the built-in Delete/Clear. */
  actions?: React.ReactNode;
}): React.ReactElement {
  const t = useBaseT();
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
  listItems: readonly ListRenderItem<TRow>[];
  tableScrollRef: React.RefObject<HTMLDivElement | null>;
  rowVirtualizer: Virtualizer<HTMLDivElement, Element>;
  visibleColumnCount: number;
  allPageSelected: boolean;
  somePageSelected: boolean;
  onPageSelectionChange: (checked: boolean) => void;
  visibleFields?: readonly VisibleFieldOption[];
  onVisibleFieldToggle?: (id: string, visible: boolean) => void;
  resourceView: ResourceViewContextValue;
  interactive: boolean;
  selectable?: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  draggableRow?: (row: TRow) => DndPayload | null;
  emptyMessage: ListEmptyContent;
  fetching: boolean;
  footerAggregate?: AggregateBucket | null;
  /** Expanded group keys; when provided, group headers become collapse toggles. */
  expandedKeys?: ReadonlySet<string>;
  onToggleGroup?: (key: string) => void;
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
  visibleFields = [],
  onVisibleFieldToggle,
  resourceView,
  interactive,
  selectable = true,
  rowHref,
  onRowClick,
  draggableRow,
  emptyMessage,
  fetching,
  footerAggregate,
  expandedKeys,
  onToggleGroup,
}: FlatListBodyProps<TRow>): React.ReactElement {
  const t = useBaseT();
  const colSpan = Math.max(1, visibleColumnCount + (selectable ? 1 : 0));
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
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {fetching && rowModels.length === 0 ? (
            <ListSkeletonRows
              table={table}
              selectable={selectable}
              loadingLabel={t("list.loading")}
            />
          ) : rowModels.length === 0 ? (
            <TableRow>
              <TableCell
                colSpan={colSpan}
                className="py-8 text-center text-fg-muted"
              >
                <ListEmpty>{emptyMessage}</ListEmpty>
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
                      resourceView,
                      interactive,
                      selectable,
                      rowHref,
                      onRowClick,
                      draggableRow,
                      expandedKeys,
                      onToggleGroup,
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
          <FlatMeasureFooter
            table={table}
            measures={measures}
            aggregate={footerAggregate}
            selectable={selectable}
          />
        ) : null}
      </Table>
    </div>
  );
}

function FlatMeasureFooter<TRow extends Row>({
  table,
  measures,
  aggregate,
  selectable,
}: {
  table: TableModel<TRow>;
  measures: readonly GroupMeasure[];
  aggregate: AggregateBucket;
  selectable: boolean;
}): React.ReactElement {
  const t = useBaseT();
  const byColumn = new Map(measures.map((measure) => [measure.columnId, measure]));
  return (
    <TableFooter>
      <TableRow>
        {selectable ? <TableCell className="w-8" /> : null}
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
      </TableRow>
    </TableFooter>
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
  const t = useBaseT();
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

export function buildColumns<TRow extends Row>(
  columns: readonly ColumnDescriptor<TRow>[],
  resourceView: ResourceViewContextValue,
): ColumnDef<TRow>[] {
  return columns.map((column) => ({
    id: column.field,
    header: () => (
      <SortHeader column={column} resourceView={resourceView}>
        {column.header ?? column.field}
      </SortHeader>
    ),
    cell: ({ row }) => (
      <ListCellContent column={column} row={row.original} />
    ),
    meta: {
      align: column.align ?? "left",
      label: column.header ?? column.field,
      field: column.field,
      aggregate: column.aggregate,
    },
  }));
}

export function ListCellContent<TRow extends Row>({
  column,
  row,
}: {
  column: ColumnDescriptor<TRow>;
  row: TRow;
}): React.ReactNode {
  const widget = useResolvedWidget(column.widget ?? "");
  if (!column.render && widget?.cell) {
    const Cell = widget.cell;
    return (
      <Cell
        value={readPath(row, column.field)}
        row={row}
        field={{
          name: column.field,
          label: column.header,
          options: column.options,
          tone: column.tone,
        }}
        readOnly
      />
    );
  }
  return cellContent(column, row);
}

function SortHeader<TRow extends Row>({
  column,
  resourceView,
  children,
}: {
  column: ColumnDescriptor<TRow>;
  resourceView: ResourceViewContextValue;
  children: React.ReactNode;
}): React.ReactElement {
  const t = useBaseT();
  if (column.sortable === false) return <>{children}</>;
  const sort = resourceView.state.sort;
  const active = sort?.field === column.field;
  const iconName = !active
    ? "arrow-up-down"
    : sort.dir === "asc"
      ? "arrow-up"
      : "arrow-down";
  const label = columnLabelText(column);
  const sortKey = !active
    ? "list.sortNotSorted"
    : sort.dir === "asc"
      ? "list.sortAscending"
      : "list.sortDescending";
  return (
    <button
      type="button"
      className="inline-flex min-w-0 items-center gap-1 rounded text-left outline-none hover:text-fg focus-visible:focus-ring"
      aria-label={t(sortKey, { label })}
      onClick={() => resourceView.setSort(nextSort(sort, column.field))}
    >
      <span className="truncate">{children}</span>
      <Glyph name={iconName} className="size-3 text-fg-subtle" />
    </button>
  );
}

export function RecordRow<TRow extends Row>({
  row,
  resourceView,
  interactive,
  selectable = true,
  rowHref,
  onRowClick,
  onRecordOpen,
  draggableRow,
}: {
  row: TableRowModel<TRow>;
  resourceView: ResourceViewContextValue;
  interactive: boolean;
  selectable?: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  onRecordOpen?: (row: TRow) => void;
  draggableRow?: (row: TRow) => DndPayload | null;
}): React.ReactElement {
  const dragProps = dragSourceProps(draggableRow?.(row.original) ?? null);
  const href = rowHref?.(row.original);
  if (href) {
    return (
      <LinkedRecordRow
        row={row}
        resourceView={resourceView}
        selectable={selectable}
        href={href}
        onRecordOpen={onRecordOpen}
        dragProps={dragProps}
      />
    );
  }
  return (
    <PlainRecordRow
      row={row}
      resourceView={resourceView}
      interactive={interactive}
      selectable={selectable}
      onRowClick={onRowClick}
      onRecordOpen={onRecordOpen}
      dragProps={dragProps}
    />
  );
}

function LinkedRecordRow<TRow extends Row>({
  row,
  resourceView,
  selectable,
  href,
  onRecordOpen,
  dragProps,
}: {
  row: TableRowModel<TRow>;
  resourceView: ResourceViewContextValue;
  selectable: boolean;
  href: string;
  onRecordOpen?: (row: TRow) => void;
  dragProps?: DragSourceProps;
}): React.ReactElement {
  const t = useBaseT();
  const id = row.id;
  const selected = resourceView.state.selectedIds.has(id);
  const navigate = useNavigate();
  const openHref = React.useCallback(
    (event: React.MouseEvent<HTMLTableRowElement>) => {
      if (isInteractiveTarget(event.target)) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        window.open(href, "_blank", "noopener");
        return;
      }
      event.preventDefault();
      onRecordOpen?.(row.original);
      void navigate({ to: href });
    },
    [href, navigate, onRecordOpen, row.original],
  );
  return (
    <TableRow
      {...dragProps}
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
        onRecordOpen?.(row.original);
        void navigate({ to: href });
      }}
    >
      {selectable ? (
        <TableCell className="w-8">
          <Checkbox
            size="sm"
            aria-label={t("list.selectRow")}
            checked={selected}
            onClick={(event) => event.stopPropagation()}
            onCheckedChange={(checked) =>
              resourceView.toggleSelectedId(id, checked)
            }
          />
        </TableCell>
      ) : null}
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
  resourceView,
  interactive,
  selectable,
  onRowClick,
  onRecordOpen,
  dragProps,
}: {
  row: TableRowModel<TRow>;
  resourceView: ResourceViewContextValue;
  interactive: boolean;
  selectable: boolean;
  onRowClick?: (row: TRow) => void;
  onRecordOpen?: (row: TRow) => void;
  dragProps?: DragSourceProps;
}): React.ReactElement {
  const t = useBaseT();
  const id = row.id;
  const selected = resourceView.state.selectedIds.has(id);
  return (
    <TableRow
      {...dragProps}
      interactive={interactive}
      data-selected={selected ? "" : undefined}
      onClick={onRowClick ? () => {
        onRecordOpen?.(row.original);
        onRowClick(row.original);
      } : undefined}
    >
      {selectable ? (
        <TableCell className="w-8">
          <Checkbox
            size="sm"
            aria-label={t("list.selectRow")}
            checked={selected}
            onClick={(event) => event.stopPropagation()}
            onCheckedChange={(checked) =>
              resourceView.toggleSelectedId(id, checked)
            }
          />
        </TableCell>
      ) : null}
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
                onRecordOpen?.(row.original);
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
  resourceView,
  interactive,
  selectable,
  rowHref,
  onRowClick,
  draggableRow,
  expandedKeys,
  onToggleGroup,
}: {
  item: ListRenderItem<TRow>;
  colSpan: number;
  resourceView: ResourceViewContextValue;
  interactive: boolean;
  selectable: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  draggableRow?: (row: TRow) => DndPayload | null;
  expandedKeys?: ReadonlySet<string>;
  onToggleGroup?: (key: string) => void;
}): React.ReactElement {
  if (item.kind === "group") {
    return (
      <GroupHeader
        key={`group:${item.group.key}`}
        groupKey={item.group.key}
        label={item.group.label ?? ""}
        rows={item.group.rows}
        depth={item.group.depth}
        colSpan={colSpan}
        expanded={expandedKeys?.has(item.group.key) ?? true}
        onToggle={onToggleGroup}
      />
    );
  }
  return (
    <RecordRow
      key={item.row.id}
      row={item.row}
      resourceView={resourceView}
      interactive={interactive}
      selectable={selectable}
      rowHref={rowHref}
      onRowClick={onRowClick}
      draggableRow={draggableRow}
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
  groupKey,
  label,
  rows,
  depth,
  colSpan,
  expanded,
  onToggle,
}: {
  groupKey: string;
  label: string;
  rows: readonly TableRowModel<TRow>[];
  depth: number;
  colSpan: number;
  expanded: boolean;
  onToggle?: (key: string) => void;
}): React.ReactElement {
  const rowCount = rows.length;
  const indent = { paddingLeft: `calc(0.75rem + ${depth * 1.25}rem)` };
  // The chevron only appears when the header is a toggle; the lead/trailing
  // content is identical either way, so it is rendered once and the branch
  // chooses only the wrapper (interactive button vs static row).
  const content = (
    <>
      <span className="inline-flex min-w-0 items-center gap-2 font-semibold text-fg">
        {onToggle ? (
          <Glyph
            name={expanded ? "chevron-down" : "chevron-right"}
            className="size-3.5 shrink-0 text-fg-muted"
          />
        ) : null}
        <span className="min-w-0 truncate">{label}</span>
        <span className="font-normal text-fg-muted">
          {rowCount.toLocaleString()}
        </span>
      </span>
    </>
  );
  return (
    <TableRow>
      <TableCell colSpan={colSpan} className="h-8 bg-sheet-2 p-0">
        {onToggle ? (
          // aria-controls is omitted deliberately: the group's rows are loose
          // virtualized siblings with no stable container id to reference.
          <button
            type="button"
            className="flex h-8 w-full min-w-0 items-center justify-between gap-3 px-3 text-left text-13 outline-none hover:bg-inset focus-visible:focus-ring"
            style={indent}
            aria-expanded={expanded}
            onClick={() => onToggle(groupKey)}
          >
            {content}
          </button>
        ) : (
          <div
            className="flex h-8 items-center justify-between gap-3 px-3 text-13"
            style={indent}
          >
            {content}
          </div>
        )}
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

export function resourceViewGroupToAggregateDimension(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): GroupByDimension {
  const dimension = groupDimensionForGroup(group, metadata);
  const extraction = groupExtractionForGroup(dimension, group);
  return {
    field: dimension.input,
    key: extraction?.key ?? dimension.key,
    ...(extraction ? { granularity: extraction.input } : {}),
    ...(extraction?.rangeKey ? { rangeKey: extraction.rangeKey } : {}),
  };
}

export function hasuraGroupDimension(
  dimension: GroupByDimension,
): HasuraGroupDimension {
  return {
    input: dimension.field,
    key: dimension.key ?? dimension.field,
    ...(dimension.granularity ? { granularity: dimension.granularity } : {}),
    ...(dimension.rangeKey ? { rangeKey: dimension.rangeKey } : {}),
  };
}

export function hasuraGroupOrderForDimensions(
  dimensions: readonly HasuraGroupDimension[],
): readonly GroupOrder[] | undefined {
  const dimension = dimensions.length > 1
    ? dimensions[dimensions.length - 1]
    : dimensions[0];
  const field = dimension?.key ?? dimension?.input;
  return field ? [{ field, direction: "ASC", nulls: "LAST" }] : undefined;
}

/**
 * The extra group-by dimension that carries a relation group's display label —
 * the same bucket grouped by `<relation>__<label>` so the related record's name
 * rides along with its id (Odoo's `(id, display_name)`). `null` when the model
 * registers no label axis for the relation, in which case the group labels by id.
 */
export function groupLabelDimension(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): GroupByDimension | null {
  const labelKey = groupLabelKey(group, metadata);
  if (!labelKey) return null;
  const dimension = groupDimensionForField(labelKey, metadata);
  return { field: dimension.input, key: dimension.key };
}

function groupLabelKey(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): string | undefined {
  const field = group.aggregateField;
  if (!field) return undefined;
  return metadata?.fields[field]?.relationFilter?.labelKey;
}

function groupDimensionForGroup(
  group: ResourceViewGroup,
  metadata: ModelMetadata | null,
): DataResourceGroupDimensionMetadata {
  return groupDimensionForField(group.aggregateField ?? group.field, metadata);
}

function groupDimensionForField(
  field: string,
  metadata: ModelMetadata | null,
): DataResourceGroupDimensionMetadata {
  const dimension = resourceGroupDimensionForField(field, metadata);
  if (!dimension) {
    const model = metadata?.typeName ?? "unknown model";
    throw new Error(
      `Resource metadata for ${model} does not declare group dimension "${field}".`,
    );
  }
  return dimension;
}

export function resourceGroupDimensionForField(
  field: string,
  metadata: ModelMetadata | null,
): DataResourceGroupDimensionMetadata | undefined {
  const dimensions = metadata?.resource?.groupDimensions ?? [];
  const snakeField = fieldToSnake(field);
  return dimensions.find((candidate) =>
    candidate.field === field ||
    candidate.key === field ||
    fieldToSnake(candidate.field) === snakeField ||
    fieldToSnake(candidate.key) === snakeField
  );
}

function groupExtractionForGroup(
  dimension: DataResourceGroupDimensionMetadata,
  group: ResourceViewGroup,
): DataResourceGroupExtractionMetadata | null {
  if (!group.granularity) return null;
  const requested = group.granularity.toUpperCase();
  const extraction = dimension.extractions?.find(
    (candidate) =>
      candidate.name === group.granularity
      || candidate.input === requested,
  );
  if (!extraction) {
    throw new Error(
      `Resource metadata for group dimension "${dimension.field}" does not ` +
        `declare extraction "${group.granularity}".`,
    );
  }
  return extraction;
}

export function bucketValueLabels(
  bucket: AggregateBucket,
  groupStack: readonly ResourceViewGroup[],
  metadata: ModelMetadata | null = null,
): string[] {
  return groupStack.map((group) => {
    const labelKey = groupLabelKey(group, metadata);
    if (labelKey) {
      const label = bucket.key?.[groupDimensionForField(labelKey, metadata).key];
      if (label != null && label !== "") return String(label);
    }
    const dimension = resourceViewGroupToAggregateDimension(group, metadata);
    const value = bucket.key?.[dimension.key ?? dimension.field];
    return groupKey(value, group, metadata);
  });
}

export function bucketFilterForGroup(
  bucket: AggregateBucket,
  group: ResourceViewGroup | undefined,
  metadata: ModelMetadata | null,
): ResourceViewFilter | undefined {
  if (!group) return {};
  const dimensionMetadata = groupDimensionForGroup(group, metadata);
  const extraction = groupExtractionForGroup(dimensionMetadata, group);
  const filter = extraction?.filter ?? dimensionMetadata.filter;
  if (!filter) {
    throw new Error(
      `Resource metadata for group dimension "${dimensionMetadata.field}" does ` +
        "not declare a bucket filter.",
    );
  }
  if (filter.kind === "range") {
    return bucketRangeDrillDownFilter(bucket, filter, dimensionMetadata);
  }

  const value = bucket.key?.[filter.valueKey ?? extraction?.key ?? dimensionMetadata.key];
  if (value === undefined) return undefined;

  if (isNullBucketValue(value, filter)) {
    return bucketNullFilter(filter);
  }

  return bucketEqualityFilter(filter, bucketFilterValue(value, filter));
}

function bucketRangeDrillDownFilter(
  bucket: AggregateBucket,
  filter: DataResourceGroupBucketFilterMetadata,
  dimension: DataResourceGroupDimensionMetadata,
): ResourceViewFilter | undefined {
  const rangeValue = filter.rangeKey ? bucket.key?.[filter.rangeKey] : undefined;
  const scalar = dateRangeScalar(dimension);
  const range = scalar ? bucketRangeFilter(rangeValue, scalar) : null;
  if (range) return { [filter.field]: range };

  const value = filter.valueKey ? bucket.key?.[filter.valueKey] : undefined;
  if (isNullBucketValue(value, filter)) return bucketNullFilter(filter);
  if (value === undefined) return undefined;
  throw new Error(
    `Group bucket for "${dimension.field}" did not include declared range key ` +
      `"${filter.rangeKey ?? ""}".`,
  );
}

function dateRangeScalar(
  dimension: DataResourceGroupDimensionMetadata,
): "Date" | "DateTime" | null {
  return dimension.scalar === "Date" || dimension.scalar === "DateTime"
    ? dimension.scalar
    : null;
}

function bucketEqualityFilter(
  filter: DataResourceGroupBucketFilterMetadata,
  value: unknown,
): ResourceViewFilter {
  if (filter.lookup) return { [filter.field]: { [filter.lookup]: value } };
  return { [filter.field]: value as ResourceViewFilter[string] };
}

function bucketFilterValue(
  value: unknown,
  filter: DataResourceGroupBucketFilterMetadata,
): unknown {
  const mapped = filter.valueMap?.find((item) => Object.is(item.from, value));
  if (mapped) return mapped.to;
  if (filter.valueTransform === "json") return jsonBucketValue(value);
  return value;
}

function isNullBucketValue(
  value: unknown,
  filter: DataResourceGroupBucketFilterMetadata,
): boolean {
  return value === null || (filter.kind === "range" && value === "");
}

function bucketNullFilter(
  filter: DataResourceGroupBucketFilterMetadata,
): ResourceViewFilter {
  return { [filter.field]: { [filter.nullLookup ?? "isNull"]: true } };
}

function jsonBucketValue(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed || !/^[\[{"]|^(true|false|null|-?\d)/.test(trimmed)) {
    return value;
  }
  try {
    return JSON.parse(trimmed);
  } catch {
    return value;
  }
}

function bucketRangeFilter(
  value: unknown,
  scalar: "Date" | "DateTime",
): ResourceViewFilter[string] | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const range = value as Record<string, unknown>;
  const from = dateBucketBoundary(range.from, scalar);
  const to = dateBucketBoundary(range.to, scalar);
  return from && to ? { gte: from, lt: to } : null;
}

function dateBucketBoundary(
  value: unknown,
  scalar: "Date" | "DateTime",
): string | null {
  const date = dateBucketStart(value);
  return date ? formatDateBoundary(date, scalar) : null;
}

function dateBucketStart(value: unknown): Date | null {
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (typeof value === "number") {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date;
  }
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const normalized = normalizeDateBucketValue(trimmed);
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function normalizeDateBucketValue(value: string): string {
  const withTimeSeparator = value.replace(
    /^(\d{4}-\d{2}-\d{2})\s+(\d)/,
    "$1T$2",
  );
  if (/^\d{4}-\d{2}-\d{2}$/.test(withTimeSeparator)) {
    return `${withTimeSeparator}T00:00:00.000Z`;
  }
  if (
    /^\d{4}-\d{2}-\d{2}T/.test(withTimeSeparator) &&
    !/(Z|[+-]\d{2}:?\d{2})$/.test(withTimeSeparator)
  ) {
    return `${withTimeSeparator}Z`;
  }
  return withTimeSeparator;
}

function formatDateBoundary(date: Date, scalar: "Date" | "DateTime"): string {
  if (scalar === "Date") {
    return [
      date.getUTCFullYear(),
      String(date.getUTCMonth() + 1).padStart(2, "0"),
      String(date.getUTCDate()).padStart(2, "0"),
    ].join("-");
  }
  return date.toISOString();
}

export function groupKey(
  value: unknown,
  group: ResourceViewGroup,
  metadata: ModelMetadata | null = null,
): string {
  if (value == null) return "No value";
  const enumLabel = typeof value === "string"
    ? enumLabelFromMetadata(metadata, group.field, value)
    : null;
  if (enumLabel) return enumLabel;
  const date = dateFromUnknown(value);
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

function enumLabelFromMetadata(
  metadata: ModelMetadata | null,
  field: string,
  value: string,
): string | null {
  const fieldMetadata = metadata?.fields[field];
  const values = fieldMetadata?.values ?? [];
  const normalized = normalizeEnumValue(value);
  const option = values.find(
    (candidate) =>
      candidate.value === value
      || normalizeEnumValue(candidate.value) === normalized,
  );
  return option ? enumValueLabel(option) : null;
}

function normalizeEnumValue(value: string): string {
  return value.trim().replace(/[\s-]+/g, "_").toLowerCase();
}

export function cellContent<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
  row: TRow,
): React.ReactNode {
  if (column.render) return column.render(row);
  const value = readPath(row, column.field);
  const tone = columnTone(column, value);
  if (tone) {
    const label = value == null ? "" : String(value);
    return <Badge tone={tone}>{label ? statusLabel(label) : "-"}</Badge>;
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
  const date = looksLikeDateField(column.field) ? dateFromUnknown(value) : null;
  if (date) return <RelativeTime value={date} />;
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

export function ariaSortForColumn<TRow extends Row>(
  column: TableColumn<TRow, unknown>,
  resourceView: ResourceViewContextValue,
): React.AriaAttributes["aria-sort"] {
  const field = columnMeta(column.columnDef).field ?? column.id;
  if (resourceView.state.sort?.field !== field) return "none";
  return resourceView.state.sort.dir === "asc" ? "ascending" : "descending";
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
      unit: "",
    });
  }
  return measures;
}

export function hasuraMeasuresFromGroupMeasures(
  measures: readonly GroupMeasure[],
  metadata: ModelMetadata | null,
): readonly GroupMeasure[] {
  if (measures.length === 0) return measures;
  return measures.map((measure) => {
    const input = hasuraMeasureInput(measure, metadata);
    return input === measure.field
      ? measure
      : { ...measure, field: input, input };
  });
}

function hasuraMeasureInput(
  measure: Pick<GroupMeasure, "op" | "field">,
  metadata: ModelMetadata | null,
): string {
  const snakeField = fieldToSnake(measure.field);
  const declared = metadata?.resource?.aggregateMeasures?.find(
    (candidate) =>
      candidate.op === measure.op &&
      (candidate.field === measure.field || candidate.field === snakeField),
  );
  return declared?.input ?? declared?.field ?? snakeField;
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

function columnLabelText<TRow extends Row>(
  column: ColumnDescriptor<TRow>,
): string {
  const header = column.header;
  if (typeof header === "string") return header;
  if (typeof header === "number") return String(header);
  return groupFieldLabel(column.field);
}

export function measureValue(
  bucket: AggregateBucket,
  measure: Pick<GroupMeasure, "op" | "field">,
): unknown {
  if (measure.op === "count") return bucket.count;
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
  current: ResourceViewContextValue["state"]["sort"],
  field: string,
): ResourceViewContextValue["state"]["sort"] {
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

export function fieldToSnake(field: string): string {
  return field
    // A `_<Capital>` is Strawberry's camel form of a Django `__` relation
    // path (e.g. `oauthClient_IsEnabled` ← `oauth_client__is_enabled`):
    // restore the double underscore so a to-one group axis round-trips to
    // its backend enum (`OAUTH_CLIENT__IS_ENABLED`). A no-op for ordinary
    // camelCase fields, which never contain `_<Capital>`.
    .replace(/_([A-Z])/g, "__$1")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/[-\s]+/g, "_")
    .toLowerCase();
}

export function groupFieldLabel(field: string): string {
  const label = titleCase(field);
  return label.endsWith(" At") ? label.slice(0, -3) : label;
}

/**
 * The display label for an enum metadata value: its authored description where
 * the resource artifact provides one, otherwise the humanized value.
 */
export function enumValueLabel(value: ModelEnumValueMetadata): string {
  return value.description ?? statusLabel(value.value);
}

/** The flush "Loading…" footer shown under a list layout while a page fetches. */
export function ListLoadingFooter(): React.ReactElement {
  const t = useBaseT();
  return (
    <div className="flex items-center justify-center gap-2 border-t border-border px-3 py-4 text-13 text-fg-muted">
      <Spinner size="sm" />
      {t("list.loading")}
    </div>
  );
}

/** Table-shaped loading body used while a list fetches its first page. */
export function ListSkeletonRows<TRow extends Row>({
  table,
  selectable = true,
  rowCount = 8,
  loadingLabel,
}: {
  table: TableModel<TRow>;
  selectable?: boolean;
  rowCount?: number;
  loadingLabel?: React.ReactNode;
}): React.ReactElement {
  const columns = table.getVisibleLeafColumns();
  const colSpan = columns.length + (selectable ? 1 : 0);
  return (
    <>
      {loadingLabel ? (
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
      ) : null}
      {Array.from({ length: Math.max(1, rowCount) }, (_, rowIndex) => (
        <TableRow key={rowIndex} aria-hidden="true">
          {selectable ? (
            <TableCell className="w-8">
              <Skeleton className="size-3.5 rounded-[3px]" />
            </TableCell>
          ) : null}
          {columns.map((column, columnIndex) => {
            const align = alignOf(column.columnDef);
            return (
              <TableCell key={column.id} className={ALIGN_CLASS[align]}>
                <Skeleton
                  shape="text"
                  size="sm"
                  className={cn(
                    skeletonCellWidth(rowIndex + columnIndex),
                    align === "right" && "ml-auto",
                    align === "center" && "mx-auto",
                  )}
                />
              </TableCell>
            );
          })}
        </TableRow>
      ))}
    </>
  );
}

function skeletonCellWidth(index: number): string {
  const widths = ["w-4/5", "w-2/3", "w-1/2", "w-24", "w-32"] as const;
  return widths[index % widths.length] ?? "w-2/3";
}

/** The centered empty body shared by the table, gallery, timeline, tree, and board views. */
export function ListEmpty({
  children,
  className,
}: {
  children: ListEmptyContent;
  className?: string;
}): React.ReactElement {
  if (isListEmptyState(children)) {
    return (
      <div
        className={cn(
          "grid h-full place-content-center text-center",
          className,
        )}
      >
        <EmptyState
          actions={children.actions ?? renderListEmptyAction(children.action)}
          className="min-h-0 p-6 shadow-none"
          description={children.description}
          icon={children.icon}
          title={children.title}
        />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "grid h-full place-content-center text-center text-13 text-fg-muted",
        className,
      )}
    >
      {children}
    </div>
  );
}

function isListEmptyState(value: ListEmptyContent): value is ListEmptyState {
  return (
    typeof value === "object"
    && value !== null
    && !Array.isArray(value)
    && !React.isValidElement(value)
    && "title" in value
  );
}

function renderListEmptyAction(
  action: ListEmptyAction | undefined,
): React.ReactNode {
  if (!action) return null;
  const variant: ButtonVariant = action.variant ?? "primary";
  const content = (
    <>
      {typeof action.icon === "string" ? <Glyph name={action.icon} /> : action.icon}
      {action.label}
    </>
  );
  if (action.href) {
    if (isInternalHref(action.href)) {
      return (
        <Link className={buttonVariants({ variant })} to={action.href}>
          {content}
        </Link>
      );
    }
    return (
      <a className={buttonVariants({ variant })} href={action.href}>
        {content}
      </a>
    );
  }
  return (
    <Button onClick={action.onClick} variant={variant}>
      {content}
    </Button>
  );
}

function isInternalHref(href: string): boolean {
  return href.startsWith("/") && !href.startsWith("//");
}

export function looksLikeDateField(field: string): boolean {
  return /(?:At|Date|On)$/.test(field);
}

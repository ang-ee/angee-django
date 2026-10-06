import * as React from "react";
import { useInAppLink } from "../../../lib/in-app-link";
import { type Row as TableRowModel } from "@tanstack/react-table";
import type { Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { useRuntimeViewAs } from "../../../runtime";
import { dragSourceProps, useDropTarget, type DndPayload } from "../../../lib/dnd";
import { Glyph } from "../../../chrome/Glyph";
import { Button } from "../../../ui/button";
import { cn } from "../../../lib/cn";
import { Checkbox } from "../../../ui/checkbox";
import { TableCell, TableHead, TableRow } from "../../../ui/table";
import type { ResourceViewContextValue } from "../resource-view-context";
import type { ResourceViewGroup } from "../resource-view-model";
import {
  columnHasInteractiveContent,
  alignOf,
  isInteractiveTarget,
  renderCell,
  rowActionLabelForTableColumn,
} from "./cell-utils";
import { GroupHeader } from "./grouping";
import { ALIGN_CLASS } from "./types";
interface RowReorder {
  type: string;
  onReorder: (fromId: string, toId: string) => void;
  previousId?: string;
  nextId?: string;
  /** The row keeps its place: a lock replaces its handle, though others may still move past it. */
  locked?: boolean;
}

function RecordRowInner<TRow extends Row>({
  row,
  selected,
  onToggleSelected,
  interactive,
  selectable = true,
  reserveLeadingColumn = false,
  rowHref,
  onRowClick,
  onRecordOpen,
  active,
  draggableRow,
  renderRowActions,
  reorder,
}: {
  row: TableRowModel<TRow>;
  selected: boolean;
  onToggleSelected: (id: string, selected?: boolean) => void;
  interactive: boolean;
  selectable?: boolean;
  /**
   * Emit the leading 32px cell even when the row is not `selectable`, as an empty
   * spacer in place of the checkbox. The flat body has no leading column unless
   * selectable, so it leaves this unset; the grouped body's `<colgroup>` and header
   * reserve that column for the group chevron on *every* row, so a grouped record
   * row must occupy it or its content cells slide left under `table-fixed`.
   */
  reserveLeadingColumn?: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  onRecordOpen?: (row: TRow) => void;
  active?: boolean;
  draggableRow?: (row: TRow) => DndPayload | null;
  renderRowActions?: (row: TRow) => React.ReactNode;
  reorder?: RowReorder;
}): React.ReactElement {
  const t = useUiT();
  const preview = useRuntimeViewAs();
  const blocked = Boolean(preview.viewAs || preview.pending);
  const { dropProps, isOver } = useDropTarget<string>({
    accept: reorder?.type,
    canDrop: (payload) => !blocked && Boolean(reorder) && typeof payload.data === "string" && payload.data !== row.id,
    onDrop: (payload) => reorder?.onReorder(payload.data, row.id),
  });
  const dragProps = {
    ...dragSourceProps(draggableRow?.(row.original) ?? null),
    ...(reorder ? dropProps : {}),
    "data-drop-target": isOver ? "" : undefined,
  };
  const handleDragProps = dragSourceProps(
    reorder && !reorder.locked && !blocked ? { type: reorder.type, data: row.id } : null,
  );
  const reorderCell = reorder?.locked ? <TableCell className="w-8">
    <span role="img" aria-label={t("list.lockedRow")} className="grid h-7 place-content-center text-fg-subtle">
      <Glyph name="lock" decorative />
    </span>
  </TableCell> : reorder ? <TableCell className="w-8">
    <Button type="button" variant="ghost" size="iconSm" aria-label={t("list.reorderRow")}
      disabled={blocked}
      title={t("list.reorderRowHint")}
      className="cursor-grab active:cursor-grabbing"
      {...handleDragProps}
      onDragStart={(event) => {
        event.stopPropagation();
        if (blocked) event.preventDefault();
        else handleDragProps?.onDragStart(event);
      }}
      onKeyDown={(event) => {
        if (blocked || !event.altKey || !["ArrowUp", "ArrowDown"].includes(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        const targetId = event.key === "ArrowUp" ? reorder.previousId : reorder.nextId;
        if (targetId) reorder.onReorder(row.id, targetId);
      }}
    ><Glyph name="grip-vertical" decorative /></Button>
  </TableCell> : null;
  const href = rowHref?.(row.original);
  if (href) {
    return (
      <LinkedRecordRow
        row={row}
        selected={selected}
        onToggleSelected={onToggleSelected}
        selectable={selectable}
        reserveLeadingColumn={reserveLeadingColumn}
        href={href}
        onRecordOpen={onRecordOpen}
        active={active}
        dragProps={dragProps}
        reorderCell={reorderCell}
        rowActions={renderRowActions?.(row.original)}
      />
    );
  }
  return (
    <PlainRecordRow
      row={row}
      selected={selected}
      onToggleSelected={onToggleSelected}
      interactive={interactive}
      selectable={selectable}
      reserveLeadingColumn={reserveLeadingColumn}
      onRowClick={onRowClick}
      onRecordOpen={onRecordOpen}
      active={active}
      dragProps={dragProps}
      reorderCell={reorderCell}
      rowActions={renderRowActions?.(row.original)}
    />
  );
}

// Memoised so a selection toggle re-renders only the affected row: `selected` is
// the sole per-row-changing prop, `onToggleSelected` is the stable
// `toggleSelectedId`, and the rest (row, callbacks) are stable across toggles
// because they originate above the selection-context boundary.
export const RecordRow = React.memo(RecordRowInner) as typeof RecordRowInner;

function LinkedRecordRow<TRow extends Row>({
  row,
  selected,
  onToggleSelected,
  selectable,
  reserveLeadingColumn,
  href,
  onRecordOpen,
  active = false,
  dragProps,
  reorderCell,
  rowActions,
}: {
  row: TableRowModel<TRow>;
  selected: boolean;
  onToggleSelected: (id: string, selected?: boolean) => void;
  selectable: boolean;
  /** See {@link RecordRow}: reserve the grouped chevron column with a spacer when not selectable. */
  reserveLeadingColumn: boolean;
  href: string;
  onRecordOpen?: (row: TRow) => void;
  active?: boolean;
  dragProps?: React.HTMLAttributes<HTMLTableRowElement>;
  reorderCell?: React.ReactNode;
  rowActions?: React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  const id = row.id;
  const firstCell = row.getVisibleCells()[0];
  const ownsControls =
    firstCell && columnHasInteractiveContent(firstCell.column.columnDef);
  const link = React.useRef<HTMLAnchorElement>(null);
  const openLink = useInAppLink(href, undefined, { onFollow: () => onRecordOpen?.(row.original) });
  const openRow = (event: React.MouseEvent<HTMLTableRowElement>) => {
    if (event.defaultPrevented || isInteractiveTarget(event.target)) return;
    // A table row has no native new-tab behavior; its actual anchor does.
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      window.open(href, "_blank", "noopener");
      return;
    }
    link.current?.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button: event.button }));
  };
  return (
    <TableRow
      {...dragProps}
      className="group/record data-[drop-target]:bg-brand-soft"
      interactive
      aria-current={active ? "true" : undefined}
      data-selected={selected ? "" : undefined}
      onClick={openRow}
      tabIndex={ownsControls ? 0 : undefined}
      onKeyDown={
        ownsControls
          ? (event) => {
              if (event.target !== event.currentTarget || event.key !== "Enter")
                return;
              event.preventDefault();
              link.current?.click();
            }
          : undefined
      }
    >
      {reorderCell}
      {selectable ? (
        <LeadingSelectionCell
          grouped={reserveLeadingColumn}
          label={t("list.selectRow")}
          checked={selected}
          onCheckedChange={(checked) => onToggleSelected(id, checked)}
        />
      ) : reserveLeadingColumn ? (
        <TableCell className="w-8" />
      ) : null}
      {row.getVisibleCells().map((cell, index) => (
        <TableCell
          key={cell.id}
          className={ALIGN_CLASS[alignOf(cell.column.columnDef)]}
          style={{ minWidth: cell.column.columnDef.minSize }}
        >
          {index === 0 &&
          !columnHasInteractiveContent(cell.column.columnDef) ? (
            <a
              ref={link}
              href={href}
              className="block min-w-0 rounded-4 text-inherit outline-none focus-visible:focus-ring"
              aria-label={t("list.openRecord", {
                label: rowActionLabelForTableColumn(
                  cell.column,
                  row.original,
                  t,
                ),
              })}
              {...openLink}
            >
              {renderCell(cell)}
            </a>
          ) : (
            <>
              {index === 0 ? <a ref={link} href={href} hidden tabIndex={-1} onClick={openLink.onClick} /> : null}
              {renderCell(cell)}
            </>
          )}
        </TableCell>
      ))}
      {rowActions !== undefined ? (
        <TableCell className="text-right">{rowActions}</TableCell>
      ) : null}
    </TableRow>
  );
}

function PlainRecordRow<TRow extends Row>({
  row,
  selected,
  onToggleSelected,
  interactive,
  selectable,
  reserveLeadingColumn,
  onRowClick,
  onRecordOpen,
  active = false,
  dragProps,
  reorderCell,
  rowActions,
}: {
  row: TableRowModel<TRow>;
  selected: boolean;
  onToggleSelected: (id: string, selected?: boolean) => void;
  interactive: boolean;
  selectable: boolean;
  /** See {@link RecordRow}: reserve the grouped chevron column with a spacer when not selectable. */
  reserveLeadingColumn: boolean;
  onRowClick?: (row: TRow) => void;
  onRecordOpen?: (row: TRow) => void;
  active?: boolean;
  dragProps?: React.HTMLAttributes<HTMLTableRowElement>;
  reorderCell?: React.ReactNode;
  rowActions?: React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  const id = row.id;
  const firstCell = row.getVisibleCells()[0];
  const ownsControls =
    firstCell && columnHasInteractiveContent(firstCell.column.columnDef);
  return (
    <TableRow
      {...dragProps}
      className="group/record data-[drop-target]:bg-brand-soft"
      interactive={interactive}
      aria-current={active ? "true" : undefined}
      data-selected={selected ? "" : undefined}
      tabIndex={interactive && ownsControls && onRowClick ? 0 : undefined}
      onKeyDown={
        interactive && ownsControls && onRowClick
          ? (event) => {
              if (
                event.target !== event.currentTarget ||
                !["Enter", " "].includes(event.key)
              )
                return;
              event.preventDefault();
              onRecordOpen?.(row.original);
              onRowClick(row.original);
            }
          : undefined
      }
      onClick={
        onRowClick
          ? (event) => {
              if (isInteractiveTarget(event.target)) return;
              onRecordOpen?.(row.original);
              onRowClick(row.original);
            }
          : undefined
      }
    >
      {reorderCell}
      {selectable ? (
        <LeadingSelectionCell
          grouped={reserveLeadingColumn}
          label={t("list.selectRow")}
          checked={selected}
          onCheckedChange={(checked) => onToggleSelected(id, checked)}
        />
      ) : reserveLeadingColumn ? (
        <TableCell className="w-8" />
      ) : null}
      {row.getVisibleCells().map((cell, index) => (
        <TableCell
          key={cell.id}
          className={ALIGN_CLASS[alignOf(cell.column.columnDef)]}
          style={{ minWidth: cell.column.columnDef.minSize }}
        >
          {interactive &&
          index === 0 &&
          onRowClick &&
          !columnHasInteractiveContent(cell.column.columnDef) ? (
            <button
              type="button"
              className="block w-full min-w-0 rounded-4 text-left text-inherit outline-none focus-visible:focus-ring"
              aria-label={t("list.openRecord", {
                label: rowActionLabelForTableColumn(
                  cell.column,
                  row.original,
                  t,
                ),
              })}
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
      {rowActions !== undefined ? (
        <TableCell className="text-right">{rowActions}</TableCell>
      ) : null}
    </TableRow>
  );
}

export function renderListRow<TRow extends Row>({
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
  reorder,
}: {
  row: TableRowModel<TRow>;
  colSpan: number;
  resourceView: ResourceViewContextValue;
  groupStack: readonly ResourceViewGroup[];
  interactive: boolean;
  selectable: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  activeRowId?: string | null;
  draggableRow?: (row: TRow) => DndPayload | null;
  renderRowActions?: (row: TRow) => React.ReactNode;
  reorder?: RowReorder;
}): React.ReactElement {
  if (row.getIsGrouped()) {
    return (
      <GroupHeader
        key={row.id}
        row={row}
        colSpan={colSpan}
        groupStack={groupStack}
      />
    );
  }
  return (
    <RecordRow
      key={row.id}
      row={row}
      selected={Boolean(resourceView.state.rowSelection[row.id])}
      onToggleSelected={resourceView.toggleSelectedId}
      interactive={interactive}
      selectable={selectable}
      rowHref={rowHref}
      onRowClick={onRowClick}
      active={activeRowId != null && String(row.original.id) === activeRowId}
      draggableRow={draggableRow}
      renderRowActions={renderRowActions}
      reorder={reorder}
    />
  );
}

export interface SelectionToggleProps {
  label: string;
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
  onCheckedChange: (checked: boolean) => void;
  className?: string;
}

/**
 * A selection checkbox whose whole area toggles it. A near miss beside the box
 * selects instead of opening the row, and the click never reaches the row.
 */
export function SelectionToggle({
  label,
  checked,
  indeterminate = false,
  disabled = false,
  onCheckedChange,
  className,
}: SelectionToggleProps): React.ReactElement {
  return (
    <div
      className={cn("flex items-center justify-center", disabled ? "cursor-not-allowed" : "cursor-pointer", className)}
      onClick={(event) => {
        event.stopPropagation();
        if (disabled || (event.target as Element).closest("[role=checkbox]")) return;
        onCheckedChange(indeterminate || !checked);
      }}
    >
      <Checkbox
        size="sm"
        aria-label={label}
        checked={checked}
        indeterminate={indeterminate}
        disabled={disabled}
        onCheckedChange={(next) => onCheckedChange(next)}
      />
    </div>
  );
}

/**
 * The leading selection cell of list rows and the list header. In a grouped
 * list the column also holds a chevron slot before the checkbox, so record,
 * group and header checkboxes stay in one vertical line.
 */
export function LeadingSelectionCell({
  head = false,
  grouped = false,
  chevron,
  ...toggle
}: SelectionToggleProps & { head?: boolean; grouped?: boolean; chevron?: React.ReactNode }): React.ReactElement {
  // Without a chevron (a grouped record row) the whole cell is the toggle; the
  // padding keeps its checkbox in line with the header's and the groups'.
  const content = (
    <div className="absolute inset-0 flex">
      {grouped && chevron ? <div className="flex w-8 shrink-0 items-center justify-center">{chevron}</div> : null}
      <SelectionToggle {...toggle} className={cn("flex-1", grouped && !chevron && "pl-8")} />
    </div>
  );
  const width = grouped ? "w-14" : "w-8";
  return head
    ? <TableHead sticky className={cn(width, "p-0")}>{content}</TableHead>
    : <TableCell className={cn("relative p-0", width)}>{content}</TableCell>;
}

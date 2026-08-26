import * as React from "react";
import { useNavigate } from "@tanstack/react-router";
import { type Row as TableRowModel } from "@tanstack/react-table";
import type { Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { dragSourceProps, type DndPayload, type DragSourceProps } from "../../../lib/dnd";
import { Checkbox } from "../../../ui/checkbox";
import { TableCell, TableRow } from "../../../ui/table";
import type { ResourceViewContextValue } from "../resource-view-context";
import type { ResourceViewGroup } from "../resource-view-model";
import { alignOf, isInteractiveTarget, renderCell, rowActionLabelForTableColumn } from "./cell-utils";
import { GroupHeader } from "./grouping";
import { ALIGN_CLASS } from "./types";
function RecordRowInner<TRow extends Row>({
  row,
  selected,
  onToggleSelected,
  interactive,
  selectable = true,
  rowHref,
  onRowClick,
  onRecordOpen,
  active,
  draggableRow,
  renderRowActions,
}: {
  row: TableRowModel<TRow>;
  selected: boolean;
  onToggleSelected: (id: string, selected?: boolean) => void;
  interactive: boolean;
  selectable?: boolean;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  onRecordOpen?: (row: TRow) => void;
  active?: boolean;
  draggableRow?: (row: TRow) => DndPayload | null;
  renderRowActions?: (row: TRow) => React.ReactNode;
}): React.ReactElement {
  const dragProps = dragSourceProps(draggableRow?.(row.original) ?? null);
  const href = rowHref?.(row.original);
  if (href) {
    return (
      <LinkedRecordRow
        row={row}
        selected={selected}
        onToggleSelected={onToggleSelected}
        selectable={selectable}
        href={href}
        onRecordOpen={onRecordOpen}
        active={active}
        dragProps={dragProps}
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
      onRowClick={onRowClick}
      onRecordOpen={onRecordOpen}
      active={active}
      dragProps={dragProps}
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
  href,
  onRecordOpen,
  active = false,
  dragProps,
  rowActions,
}: {
  row: TableRowModel<TRow>;
  selected: boolean;
  onToggleSelected: (id: string, selected?: boolean) => void;
  selectable: boolean;
  href: string;
  onRecordOpen?: (row: TRow) => void;
  active?: boolean;
  dragProps?: DragSourceProps;
  rowActions?: React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  const id = row.id;
  const navigate = useNavigate();
  const openRow = React.useCallback(
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
  const openLink = React.useCallback(
    (event: React.MouseEvent<HTMLAnchorElement>) => {
      if (
        event.defaultPrevented
        || event.button !== 0
        || event.metaKey
        || event.ctrlKey
        || event.shiftKey
        || event.altKey
      ) {
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
      aria-current={active ? "true" : undefined}
      data-selected={selected ? "" : undefined}
      onClick={openRow}
    >
      {selectable ? (
        <TableCell className="w-8">
          <Checkbox
            size="sm"
            aria-label={t("list.selectRow")}
            checked={selected}
            onClick={(event) => event.stopPropagation()}
            onCheckedChange={(checked) =>
              onToggleSelected(id, checked)
            }
          />
        </TableCell>
      ) : null}
      {row.getVisibleCells().map((cell, index) => (
        <TableCell
          key={cell.id}
          className={ALIGN_CLASS[alignOf(cell.column.columnDef)]}
        >
          {index === 0 ? (
            <a
              href={href}
              className="block min-w-0 rounded-4 text-inherit outline-none focus-visible:focus-ring"
              aria-label={t("list.openRecord", {
                label: rowActionLabelForTableColumn(cell.column, row.original, t),
              })}
              onClick={openLink}
            >
              {renderCell(cell)}
            </a>
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

function PlainRecordRow<TRow extends Row>({
  row,
  selected,
  onToggleSelected,
  interactive,
  selectable,
  onRowClick,
  onRecordOpen,
  active = false,
  dragProps,
  rowActions,
}: {
  row: TableRowModel<TRow>;
  selected: boolean;
  onToggleSelected: (id: string, selected?: boolean) => void;
  interactive: boolean;
  selectable: boolean;
  onRowClick?: (row: TRow) => void;
  onRecordOpen?: (row: TRow) => void;
  active?: boolean;
  dragProps?: DragSourceProps;
  rowActions?: React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  const id = row.id;
  return (
    <TableRow
      {...dragProps}
      interactive={interactive}
      aria-current={active ? "true" : undefined}
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
              onToggleSelected(id, checked)
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
              className="block w-full min-w-0 rounded-4 text-left text-inherit outline-none focus-visible:focus-ring"
              aria-label={t("list.openRecord", {
                label: rowActionLabelForTableColumn(cell.column, row.original, t),
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
      selected={resourceView.state.selectedIds.has(row.id)}
      onToggleSelected={resourceView.toggleSelectedId}
      interactive={interactive}
      selectable={selectable}
      rowHref={rowHref}
      onRowClick={onRowClick}
      active={activeRowId != null && String(row.original.id) === activeRowId}
      draggableRow={draggableRow}
      renderRowActions={renderRowActions}
    />
  );
}

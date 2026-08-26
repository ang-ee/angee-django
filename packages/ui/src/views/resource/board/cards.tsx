import * as React from "react";
import { useDraggable } from "@dnd-kit/core";
import { useSortable } from "@dnd-kit/sortable";
import type { Row as TableRowModel } from "@tanstack/react-table";
import { useNavigate } from "@tanstack/react-router";
import { type ModelMetadata, type Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { Glyph } from "../../../chrome/Glyph";
import { cn } from "../../../lib/cn";
import type { Tone } from "../../../lib/tones";
import type { ResourceViewGroup } from "../resource-view-model";
import { ListCellContent, readPath, type RowGroup } from "../resource-view-list-body";
import { columnTone } from "../../page";
import type { ColumnDescriptor } from "../../page";
import type { CardActionContext } from "../resource-view-types";
import { BOARD_CARD_SHELL_CLASS } from "./view";
interface BoardRowCardProps<TRow extends Row> {
  columns: readonly ColumnDescriptor<TRow>[];
  groupFields: ReadonlySet<string>;
  modelMetadata?: ModelMetadata | null;
  row: TableRowModel<TRow>;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  cardActions?: (row: TRow, context: CardActionContext) => React.ReactNode;
  cardActionContext: CardActionContext;
  dragEnabled: boolean;
  sortable: boolean;
  laneId: string;
  renderCard?: (row: TRow) => React.ReactNode;
}

export function BoardRowCard<TRow extends Row>(
  props: BoardRowCardProps<TRow>,
): React.ReactElement {
  if (props.sortable) {
    return <SortableBoardRowCard {...props} />;
  }
  return <DraggableBoardRowCard {...props} />;
}

function DraggableBoardRowCard<TRow extends Row>({
  row,
  laneId,
  ...props
}: BoardRowCardProps<TRow>): React.ReactElement {
  const drag = useDraggable({
    id: row.id,
    data: { type: "board-card", row: row.original, laneId },
    disabled: !props.dragEnabled,
  });
  return (
    <BoardRowCardContent
      {...props}
      row={row}
      laneId={laneId}
      drag={{ ...drag, transition: undefined }}
    />
  );
}

function SortableBoardRowCard<TRow extends Row>({
  row,
  laneId,
  ...props
}: BoardRowCardProps<TRow>): React.ReactElement {
  const drag = useSortable({
    id: row.id,
    data: { type: "board-card", row: row.original, laneId },
  });
  return (
    <BoardRowCardContent
      {...props}
      row={row}
      laneId={laneId}
      dragEnabled
      drag={drag}
    />
  );
}

type BoardCardDrag = Pick<
  ReturnType<typeof useSortable>,
  | "attributes"
  | "listeners"
  | "setNodeRef"
  | "setActivatorNodeRef"
  | "transform"
  | "transition"
  | "isDragging"
>;

function BoardRowCardContent<TRow extends Row>({
  columns,
  groupFields,
  modelMetadata,
  row,
  rowHref,
  onRowClick,
  cardActions,
  cardActionContext,
  dragEnabled,
  renderCard,
  drag,
}: BoardRowCardProps<TRow> & { drag: BoardCardDrag }): React.ReactElement {
  const t = useUiT();
  const href = rowHref?.(row.original);
  const actions = cardActions?.(row.original, cardActionContext);
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    transition,
    isDragging,
  } = drag;
  const style = dragEnabled && transform
    ? {
        transform: `translate3d(${transform.x}px, ${transform.y}px, 0)`,
        transition,
      }
    : undefined;
  return (
    <article
      ref={dragEnabled ? setNodeRef : undefined}
      style={style}
      className={cn(
        "board-card-grid grid min-w-0 gap-2 rounded-8 border border-border-subtle bg-sheet p-3 shadow-xs",
        isDragging
          ? "transition-none"
          : "transition hover:-translate-y-0.5 hover:border-border hover:shadow-md",
        dragEnabled && "select-none",
        isDragging && "z-10 border-border-focus shadow-lg",
      )}
    >
      <div className="flex min-w-0 max-w-full items-start gap-2">
        <div className="min-w-0 flex-1">
          <BoardCardFrame
            href={href}
            onClick={onRowClick ? () => onRowClick(row.original) : undefined}
          >
            {renderCard ? (
              renderCard(row.original)
            ) : (
              <DefaultBoardCardBody
                columns={columns}
                groupFields={groupFields}
                modelMetadata={modelMetadata}
                row={row.original}
              />
            )}
          </BoardCardFrame>
        </div>
        {dragEnabled ? (
          <button
            ref={setActivatorNodeRef}
            type="button"
            aria-label={t("board.dragCard")}
            className="grid size-7 shrink-0 touch-none cursor-grab place-content-center rounded-6 text-fg-subtle outline-none transition-colors hover:bg-inset hover:text-fg focus-visible:focus-ring active:cursor-grabbing"
            {...attributes}
            {...listeners}
          >
            <Glyph name="grip-vertical" />
          </button>
        ) : null}
      </div>
      {actions ? (
        <footer className="flex items-center justify-end gap-2 border-t border-border-subtle pt-2">
          {actions}
        </footer>
      ) : null}
    </article>
  );
}

function DefaultBoardCardBody<TRow extends Row>({
  columns,
  groupFields,
  modelMetadata,
  row,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  groupFields: ReadonlySet<string>;
  modelMetadata?: ModelMetadata | null;
  row: TRow;
}): React.ReactElement {
  const cardColumns = columns
    .filter((column) => !groupFields.has(column.field))
    .slice(0, 4);
  const [titleColumn, ...detailColumns] = cardColumns;
  return (
    <>
      {titleColumn ? (
        <span className="block min-w-0 truncate text-sm font-semibold text-fg">
          <ListCellContent
            column={titleColumn}
            row={row}
            metadata={modelMetadata}
          />
        </span>
      ) : null}
      {detailColumns.map((column) => (
        <div
          key={column.field}
          className="board-card-detail-grid grid min-w-0 items-start gap-x-3 text-13"
        >
          <span className="min-w-0 truncate text-fg-muted">
            {column.header ?? column.field}
          </span>
          <span className="min-w-0 overflow-hidden text-right text-fg [overflow-wrap:anywhere] [&>*]:max-w-full">
            <ListCellContent column={column} row={row} metadata={modelMetadata} />
          </span>
        </div>
      ))}
    </>
  );
}

function BoardCardFrame({
  href,
  onClick,
  children,
}: {
  href?: string;
  onClick?: () => void;
  children: React.ReactNode;
}): React.ReactElement {
  const navigate = useNavigate();
  const handleLinkClick = React.useCallback(
    (event: React.MouseEvent<HTMLAnchorElement>) => {
      if (
        !href
        || event.defaultPrevented
        || event.button !== 0
        || event.metaKey
        || event.ctrlKey
        || event.shiftKey
        || event.altKey
      ) {
        return;
      }
      event.preventDefault();
      void navigate({ to: href });
    },
    [href, navigate],
  );
  if (href) {
    return (
      <a href={href} className={BOARD_CARD_SHELL_CLASS} onClick={handleLinkClick}>
        {children}
      </a>
    );
  }
  if (onClick) {
    return (
      <button
        type="button"
        className={BOARD_CARD_SHELL_CLASS}
        onClick={onClick}
      >
        {children}
      </button>
    );
  }
  return <div className={BOARD_CARD_SHELL_CLASS}>{children}</div>;
}

export function laneDotTone<TRow extends Row>(
  group: RowGroup<TRow>,
  groupStack: readonly ResourceViewGroup[],
  columns: readonly ColumnDescriptor<TRow>[],
): Tone | undefined {
  const groupField = groupStack[group.depth]?.field;
  const column = groupField
    ? columns.find((candidate) => candidate.field === groupField)
    : undefined;
  if (!groupField || !column) return undefined;
  const row = group.rows[0]?.original;
  const value = row ? readPath(row, groupField) : undefined;
  return columnTone(column, value);
}

export function flattenLeaves<TRow extends Row>(group: RowGroup<TRow>): RowGroup<TRow>[] {
  if (group.children.length === 0) return [group];
  return group.children.flatMap(flattenLeaves);
}

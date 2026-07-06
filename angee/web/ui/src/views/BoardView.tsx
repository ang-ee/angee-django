// Kanban board rendering for the resource-view surface. It consumes precomputed rows
// from its parent and remains fetch-free.
import * as React from "react";
import {
  DndContext,
  pointerWithin,
  rectIntersection,
  useDraggable,
  useDroppable,
  type CollisionDetection,
  type DragEndEvent,
} from "@dnd-kit/core";
import type {
  Row as TableRowModel } from "@tanstack/react-table";
import { useNavigate } from "@tanstack/react-router";
import type { Row,
} from "@angee/metadata";

import { useUiT } from "../i18n";
import { Glyph } from "../chrome/Glyph";
import { cn } from "../lib/cn";
import { useDndKitSensors } from "../lib/dnd";
import { type Tone } from "../lib/tones";
import { CountBadge } from "../ui/badge";
import { Skeleton, SkeletonStatus } from "../ui/skeleton";
import { StatusDot } from "../ui/status-icon";
import type { ResourceViewContextValue } from "./resource-view-context";
import type { ResourceViewGroup } from "./resource-view-model";
import {
  ListCellContent,
  ListEmpty,
  readPath,
  type RowGroup,
} from "./resource-view-list-body";
import type { ListEmptyContent } from "./resource-view-types";
import { columnTone } from "./page";
import type { ColumnDescriptor } from "./page";
import type { CardActionContext } from "./resource-view-types";

const BOARD_SCROLL_SURFACE_CLASS =
  "flex items-start gap-3 p-3";
const BOARD_CARD_SHELL_CLASS =
  "block min-w-0 max-w-full rounded-8 text-left text-inherit outline-none focus-visible:focus-ring";

export interface BoardViewProps<TRow extends Row = Row> {
  columns: readonly ColumnDescriptor<TRow>[];
  groups: readonly RowGroup<TRow>[];
  resourceView: ResourceViewContextValue;
  selectedIds: ReadonlySet<string>;
  interactive: boolean;
  fetching?: boolean;
  emptyContent: ListEmptyContent;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  cardActions?: (row: TRow, context: CardActionContext) => React.ReactNode;
  cardActionContext?: CardActionContext;
  dragEnabled?: boolean;
  onCardMove?: (row: TRow, laneId: string | null) => void | Promise<void>;
  /** Override the card body (mirrors `GalleryView.renderCard`) — for a rich card
   * (description, chips, badges) instead of the default title + key/value rows. The
   * lane grouping, frame link/click, selection, and the `cardActions` footer stay. */
  renderCard?: (row: TRow) => React.ReactNode;
}

export function BoardView<TRow extends Row = Row>(
  props: BoardViewProps<TRow>,
): React.ReactElement {
  const {
    columns,
    groups,
    resourceView,
    fetching = false,
    emptyContent,
    rowHref,
    onRowClick,
    cardActions,
    cardActionContext,
    dragEnabled = false,
    onCardMove,
    renderCard,
  } = props;
  return (
    <BoardRows
      columns={columns}
      fetching={fetching}
      groups={groups}
      groupStack={resourceView.state.groupStack}
      emptyContent={emptyContent}
      rowHref={rowHref}
      onRowClick={onRowClick}
      cardActions={cardActions}
      cardActionContext={cardActionContext ?? EMPTY_CARD_ACTION_CONTEXT}
      dragEnabled={dragEnabled}
      onCardMove={onCardMove}
      renderCard={renderCard}
    />
  );
}

const EMPTY_CARD_ACTION_CONTEXT: CardActionContext = {
  refresh: () => undefined,
};

function BoardRows<TRow extends Row>({
  columns,
  fetching,
  groups,
  groupStack,
  emptyContent,
  rowHref,
  onRowClick,
  cardActions,
  cardActionContext,
  dragEnabled,
  onCardMove,
  renderCard,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  fetching: boolean;
  groups: readonly RowGroup<TRow>[];
  groupStack: readonly ResourceViewGroup[];
  emptyContent: ListEmptyContent;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  cardActions?: (row: TRow, context: CardActionContext) => React.ReactNode;
  cardActionContext: CardActionContext;
  dragEnabled: boolean;
  onCardMove?: (row: TRow, laneId: string | null) => void | Promise<void>;
  renderCard?: (row: TRow) => React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  const leaves = groups.flatMap(flattenLeaves);
  const hasDeclaredLanes = leaves.some((group) => group.declared);
  const groupFields = new Set(groupStack.map((group) => group.field));
  const moveEnabled = dragEnabled && Boolean(onCardMove);
  const sensors = useDndKitSensors(6);
  const handleDragEnd = React.useCallback(
    (event: DragEndEvent) => {
      const active = boardDragData<TRow>(event);
      const nextLaneId = event.over?.id;
      if (!active || typeof nextLaneId !== "string" || nextLaneId === active.laneId) {
        return;
      }
      void onCardMove?.(active.row, nextLaneId === "" ? null : nextLaneId);
    },
    [onCardMove],
  );
  if (!hasDeclaredLanes && leaves.every((group) => group.rows.length === 0)) {
    if (fetching) {
      return (
        <BoardSkeleton
          laneCount={groupStack.length > 0 ? 3 : 1}
          loadingLabel={t("list.loading")}
        />
      );
    }
    return <ListEmpty className="px-3 py-8">{emptyContent}</ListEmpty>;
  }
  // Kanban is most useful with an active group axis; with no group-by applied a single lane is shown.
  // The board renders the current page only (bounded by the page-size cap, MAX_PAGE_SIZE), grouped into lanes; no row virtualization is used here.
  const board = (
    <div className={BOARD_SCROLL_SURFACE_CLASS}>
      {leaves.map((group) => (
        <BoardLane
          key={group.key}
          columns={columns}
          group={group}
          groupStack={groupStack}
          groupFields={groupFields}
          rowHref={rowHref}
          onRowClick={onRowClick}
          cardActions={cardActions}
          cardActionContext={cardActionContext}
          dragEnabled={moveEnabled}
          renderCard={renderCard}
        />
      ))}
    </div>
  );
  if (!moveEnabled) return board;
  return (
    <DndContext
      sensors={sensors}
      collisionDetection={boardCollisionDetection}
      onDragEnd={handleDragEnd}
    >
      {board}
    </DndContext>
  );
}

const boardCollisionDetection: CollisionDetection = (args) => {
  const pointerCollisions = pointerWithin(args);
  return pointerCollisions.length > 0
    ? pointerCollisions
    : rectIntersection(args);
};

interface BoardDragData<TRow extends Row> {
  row: TRow;
  laneId: string;
}

function boardDragData<TRow extends Row>(
  event: DragEndEvent,
): BoardDragData<TRow> | null {
  const data = event.active.data.current;
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const record = data as Record<string, unknown>;
  if (typeof record.laneId !== "string") return null;
  if (!record.row || typeof record.row !== "object" || Array.isArray(record.row)) {
    return null;
  }
  return { laneId: record.laneId, row: record.row as TRow };
}

function BoardSkeleton({
  laneCount,
  loadingLabel,
}: {
  laneCount: number;
  loadingLabel: React.ReactNode;
}): React.ReactElement {
  return (
    <SkeletonStatus
      label={loadingLabel}
      className={BOARD_SCROLL_SURFACE_CLASS}
    >
      {Array.from({ length: Math.max(1, laneCount) }, (_, laneIndex) => (
        <section
          key={laneIndex}
          aria-hidden="true"
          className="flex w-[300px] flex-none flex-col rounded-[10px] border border-border-subtle bg-inset"
        >
          <div className="sticky top-0 z-10 flex items-center gap-2 rounded-t-[10px] bg-inset px-3 pt-3 pb-2">
            <Skeleton className="size-2.5 shrink-0 rounded-full" />
            <Skeleton
              shape="text"
              size="sm"
              className={laneIndex % 2 === 0 ? "w-28 flex-1" : "w-20 flex-1"}
            />
            <Skeleton shape="text" size="sm" className="w-5" />
          </div>
          <div className="flex flex-col gap-2 px-2 pb-2">
            {Array.from({ length: 3 }, (_, cardIndex) => (
              <article
                key={cardIndex}
                className="grid gap-2 rounded-8 border border-border-subtle bg-sheet p-3 shadow-xs"
              >
                <Skeleton
                  shape="text"
                  size="md"
                  className={cardIndex % 2 === 0 ? "w-5/6" : "w-2/3"}
                />
                <div className="grid gap-2">
                  <div className="flex items-center justify-between gap-3">
                    <Skeleton shape="text" size="sm" className="w-16" />
                    <Skeleton shape="text" size="sm" className="w-20" />
                  </div>
                  <div className="flex items-center justify-between gap-3">
                    <Skeleton shape="text" size="sm" className="w-12" />
                    <Skeleton shape="text" size="sm" className="w-24" />
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>
      ))}
    </SkeletonStatus>
  );
}

function BoardLane<TRow extends Row>({
  columns,
  group,
  groupStack,
  groupFields,
  rowHref,
  onRowClick,
  cardActions,
  cardActionContext,
  dragEnabled,
  renderCard,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  group: RowGroup<TRow>;
  groupStack: readonly ResourceViewGroup[];
  groupFields: ReadonlySet<string>;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  cardActions?: (row: TRow, context: CardActionContext) => React.ReactNode;
  cardActionContext: CardActionContext;
  dragEnabled: boolean;
  renderCard?: (row: TRow) => React.ReactNode;
}): React.ReactElement {
  const headingId = React.useId();
  const t = useUiT();
  const tone = laneDotTone(group, groupStack, columns);
  const { setNodeRef, isOver } = useDroppable({
    id: group.key,
    disabled: !dragEnabled || group.dropDisabled === true,
  });
  return (
    <section
      ref={dragEnabled ? setNodeRef : undefined}
      aria-labelledby={headingId}
      className={cn(
        "flex w-[300px] flex-none flex-col rounded-[10px] border border-border-subtle bg-inset",
        dragEnabled && "transition-colors",
        isOver && group.dropDisabled !== true && "border-border-focus bg-brand-soft/25",
      )}
    >
      <div className="sticky top-0 z-10 flex items-center gap-2 rounded-t-[10px] bg-inset px-3 pt-3 pb-2">
        {tone ? <StatusDot tone={tone} /> : null}
        <h3
          id={headingId}
          className="min-w-0 flex-1 truncate text-13 font-semibold text-fg"
        >
          {group.label ?? t("list.allRecords")}
        </h3>
        <CountBadge value={group.rows.length} />
      </div>
      <div className="flex flex-col gap-2 px-2 pb-2">
        {group.rows.map((row) => (
          <BoardRowCard
            key={row.id}
            columns={columns}
            groupFields={groupFields}
            row={row}
            rowHref={rowHref}
            onRowClick={onRowClick}
            cardActions={cardActions}
            cardActionContext={cardActionContext}
            dragEnabled={dragEnabled}
            laneId={group.key}
            renderCard={renderCard}
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
  cardActions,
  cardActionContext,
  dragEnabled,
  laneId,
  renderCard,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  groupFields: ReadonlySet<string>;
  row: TableRowModel<TRow>;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  cardActions?: (row: TRow, context: CardActionContext) => React.ReactNode;
  cardActionContext: CardActionContext;
  dragEnabled: boolean;
  laneId: string;
  renderCard?: (row: TRow) => React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  const href = rowHref?.(row.original);
  const actions = cardActions?.(row.original, cardActionContext);
  const {
    attributes,
    listeners,
    setNodeRef,
    setActivatorNodeRef,
    transform,
    isDragging,
  } = useDraggable({
    id: row.id,
    data: { row: row.original, laneId },
    disabled: !dragEnabled,
  });
  const style = dragEnabled && transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` }
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
  row,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  groupFields: ReadonlySet<string>;
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
          <ListCellContent column={titleColumn} row={row} />
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
            <ListCellContent column={column} row={row} />
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

function laneDotTone<TRow extends Row>(
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

function flattenLeaves<TRow extends Row>(group: RowGroup<TRow>): RowGroup<TRow>[] {
  if (group.children.length === 0) return [group];
  return group.children.flatMap(flattenLeaves);
}

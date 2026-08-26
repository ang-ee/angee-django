import * as React from "react";
import { DndContext, type DragEndEvent } from "@dnd-kit/core";
import type { Row as TableRowModel } from "@tanstack/react-table";
import { rowPublicId, type ModelMetadata, type Row } from "@angee/metadata";
import { useUiT } from "../../../i18n";
import { useDndKitSensors } from "../../../lib/dnd";
import type { ResourceViewContextValue } from "../resource-view-context";
import type { ResourceViewGroup } from "../resource-view-model";
import { ListEmpty, readPath, type RowGroup } from "../resource-view-list-body";
import type { ColumnDescriptor } from "../../page";
import type { BoardCardPlacement, CardActionContext, ListEmptyContent } from "../resource-view-types";
import { boardMoveForDrop } from "../board-ordering";
import { flattenLeaves } from "./cards";
import { boardCollisionDetection, boardDragData, boardDropTarget } from "./dnd";
import { BoardLane, BoardSkeleton, EMPTY_FOLD_OVERRIDES } from "./lanes";
export const BOARD_SCROLL_SURFACE_CLASS =
  "flex items-start gap-3 p-3";
export const BOARD_CARD_SHELL_CLASS =
  "block min-w-0 max-w-full rounded-8 text-left text-inherit outline-none focus-visible:focus-ring";

export interface BoardViewProps<TRow extends Row = Row> {
  columns: readonly ColumnDescriptor<TRow>[];
  groups: readonly RowGroup<TRow>[];
  resourceView: ResourceViewContextValue;
  modelMetadata?: ModelMetadata | null;
  selectedIds: ReadonlySet<string>;
  interactive: boolean;
  fetching?: boolean;
  emptyContent: ListEmptyContent;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  cardActions?: (row: TRow, context: CardActionContext) => React.ReactNode;
  cardActionContext?: CardActionContext;
  dragEnabled?: boolean;
  rankField?: string;
  optimisticPlacementByRowId?: ReadonlyMap<string, BoardCardPlacement>;
  onCardMove?: (
    row: TRow,
    laneId: string | null,
    rank?: number,
  ) => void | Promise<void>;
  /** Lane-footer create seam; the resource page opens its existing create form. */
  onCreateInLane?: (laneId: string | null, rank?: number) => void;
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
    modelMetadata,
    fetching = false,
    emptyContent,
    rowHref,
    onRowClick,
    cardActions,
    cardActionContext,
    dragEnabled = false,
    rankField,
    optimisticPlacementByRowId = EMPTY_BOARD_PLACEMENTS,
    onCardMove,
    onCreateInLane,
    renderCard,
  } = props;
  return (
    <BoardRows
      columns={columns}
      fetching={fetching}
      groups={groups}
      groupStack={resourceView.state.groupStack}
      modelMetadata={modelMetadata}
      emptyContent={emptyContent}
      rowHref={rowHref}
      onRowClick={onRowClick}
      cardActions={cardActions}
      cardActionContext={cardActionContext ?? EMPTY_CARD_ACTION_CONTEXT}
      dragEnabled={dragEnabled}
      rankField={rankField}
      optimisticPlacementByRowId={optimisticPlacementByRowId}
      onCardMove={onCardMove}
      onCreateInLane={onCreateInLane}
      renderCard={renderCard}
    />
  );
}

const EMPTY_CARD_ACTION_CONTEXT: CardActionContext = {
  refresh: () => undefined,
};

const EMPTY_BOARD_PLACEMENTS: ReadonlyMap<string, BoardCardPlacement> = new Map();

function BoardRows<TRow extends Row>({
  columns,
  fetching,
  groups,
  groupStack,
  modelMetadata,
  emptyContent,
  rowHref,
  onRowClick,
  cardActions,
  cardActionContext,
  dragEnabled,
  rankField,
  optimisticPlacementByRowId,
  onCardMove,
  onCreateInLane,
  renderCard,
}: {
  columns: readonly ColumnDescriptor<TRow>[];
  fetching: boolean;
  groups: readonly RowGroup<TRow>[];
  groupStack: readonly ResourceViewGroup[];
  modelMetadata?: ModelMetadata | null;
  emptyContent: ListEmptyContent;
  rowHref?: (row: TRow) => string;
  onRowClick?: (row: TRow) => void;
  cardActions?: (row: TRow, context: CardActionContext) => React.ReactNode;
  cardActionContext: CardActionContext;
  dragEnabled: boolean;
  rankField?: string;
  optimisticPlacementByRowId: ReadonlyMap<string, BoardCardPlacement>;
  onCardMove?: (
    row: TRow,
    laneId: string | null,
    rank?: number,
  ) => void | Promise<void>;
  onCreateInLane?: (laneId: string | null, rank?: number) => void;
  renderCard?: (row: TRow) => React.ReactNode;
}): React.ReactElement {
  const t = useUiT();
  const leaves = groups.flatMap(flattenLeaves);
  const hasDeclaredLanes = leaves.some((group) => group.declared);
  const groupFields = new Set(groupStack.map((group) => group.field));
  const moveEnabled = dragEnabled && Boolean(onCardMove);
  const sortable = moveEnabled && Boolean(rankField);
  const sensors = useDndKitSensors(6);
  const [foldOverrides, setFoldOverrides] = React.useState<
    ReadonlyMap<string, boolean>
  >(EMPTY_FOLD_OVERRIDES);
  const rankForRow = React.useCallback(
    (row: TableRowModel<TRow>): number | undefined => {
      if (!rankField) return undefined;
      const rowId = rowPublicId(row.original) ?? row.id;
      const optimisticRank = optimisticPlacementByRowId.get(rowId)?.rank;
      if (optimisticRank !== undefined) return optimisticRank;
      const rank = readPath(row.original, rankField);
      return typeof rank === "number" && Number.isFinite(rank)
        ? rank
        : undefined;
    },
    [optimisticPlacementByRowId, rankField],
  );
  const handleDragEnd = React.useCallback(
    (event: DragEndEvent) => {
      const active = boardDragData<TRow>(event);
      const target = boardDropTarget(event, leaves);
      if (!active || !target) return;
      const move = boardMoveForDrop({
        activeRowId: active.rowId,
        activeLaneId: active.laneId,
        target,
        groups: leaves,
        rankField,
        rankForRow,
      });
      if (!move) return;
      const laneId = move.laneId === "" ? null : move.laneId;
      if (move.rank === undefined) void onCardMove?.(active.row, laneId);
      else void onCardMove?.(active.row, laneId, move.rank);
    },
    [leaves, onCardMove, rankField, rankForRow],
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
          modelMetadata={modelMetadata}
          groupFields={groupFields}
          rowHref={rowHref}
          onRowClick={onRowClick}
          cardActions={cardActions}
          cardActionContext={cardActionContext}
          dragEnabled={moveEnabled}
          sortable={sortable}
          rankField={rankField}
          rankForRow={rankForRow}
          collapsed={
            foldOverrides.get(group.key) ?? group.defaultCollapsed === true
          }
          onCollapsedChange={(collapsed) => {
            setFoldOverrides((current) => {
              const next = new Map(current);
              next.set(group.key, collapsed);
              return next;
            });
          }}
          onCreateInLane={onCreateInLane}
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

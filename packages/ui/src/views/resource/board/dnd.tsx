import { pointerWithin, rectIntersection, type CollisionDetection, type DragEndEvent } from "@dnd-kit/core";
import { type Row } from "@angee/metadata";
import { type RowGroup } from "../resource-view-list-body";
import { type BoardDropTarget } from "../board-ordering";
export const boardCollisionDetection: CollisionDetection = (args) => {
  const pointerCollisions = pointerWithin(args);
  return prioritizeCardCollision(
    pointerCollisions.length > 0 ? pointerCollisions : rectIntersection(args),
  );
};

function prioritizeCardCollision<TCollision extends { id: string | number }>(
  collisions: TCollision[],
): TCollision[] {
  const cardIndex = collisions.findIndex(
    (collision) => !String(collision.id).startsWith("board-lane:"),
  );
  if (cardIndex <= 0) return collisions;
  return [
    collisions[cardIndex]!,
    ...collisions.slice(0, cardIndex),
    ...collisions.slice(cardIndex + 1),
  ];
}

interface BoardDragData<TRow extends Row> {
  row: TRow;
  rowId: string;
  laneId: string;
}

export function boardDragData<TRow extends Row>(
  event: DragEndEvent,
): BoardDragData<TRow> | null {
  const data = event.active.data.current;
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  const record = data as Record<string, unknown>;
  if (typeof record.laneId !== "string") return null;
  if (!record.row || typeof record.row !== "object" || Array.isArray(record.row)) {
    return null;
  }
  return {
    laneId: record.laneId,
    rowId: String(event.active.id),
    row: record.row as TRow,
  };
}

export function boardDropTarget<TRow extends Row>(
  event: DragEndEvent,
  groups: readonly RowGroup<TRow>[],
): BoardDropTarget | null {
  if (!event.over) return null;
  const data = event.over.data?.current;
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const record = data as Record<string, unknown>;
    if (record.type === "board-card" && typeof record.laneId === "string") {
      return { laneId: record.laneId, rowId: String(event.over.id) };
    }
    if (record.type === "board-lane" && typeof record.laneId === "string") {
      return { laneId: record.laneId };
    }
  }
  const overId = String(event.over.id);
  const cardGroup = groups.find((group) =>
    group.rows.some((row) => row.id === overId),
  );
  if (cardGroup) return { laneId: cardGroup.key, rowId: overId };
  const laneGroup = groups.find((group) => group.key === overId);
  if (laneGroup) return { laneId: laneGroup.key };
  // Rendered targets carry typed data. The id fallback keeps custom collision
  // detectors that expose lane ids interoperable with the board move seam.
  return { laneId: overId };
}

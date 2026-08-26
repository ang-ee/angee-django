import type { Row } from "@angee/metadata";
import type { Row as TableRowModel } from "@tanstack/react-table";

import type { RowGroup } from "./resource-view-list-body";

/** The clean spread owned by `FractionalRankField` on the server. */
export const FRACTIONAL_RANK_STEP = 1024;

export interface BoardDropTarget {
  laneId: string;
  rowId?: string;
}

export interface BoardDropMove {
  laneId: string;
  rank?: number;
}

/**
 * Compute the same requested rank shape as `FractionalRankField`: prepend and
 * append by one clean step, otherwise take the overflow-safe midpoint. The
 * client only proposes this value. A rounded-to-neighbour midpoint is sent as-is
 * so the server's contextual constraint/rank contract remains the authority and
 * its ordinary mutation error/refetch path handles exhaustion.
 */
export function fractionalRankBetween(
  previous: number | undefined,
  following: number | undefined,
): number {
  if (following === undefined) {
    if (previous === undefined) return FRACTIONAL_RANK_STEP;
    const candidate = previous + FRACTIONAL_RANK_STEP;
    return Number.isFinite(candidate) ? candidate : previous;
  }
  if (previous === undefined) {
    const candidate = following - FRACTIONAL_RANK_STEP;
    return Number.isFinite(candidate) ? candidate : following;
  }
  const candidate = previous / 2 + following / 2;
  return Number.isFinite(candidate) ? candidate : previous;
}

/** Resolve one drop to a lane plus its optional requested rank. */
export function boardMoveForDrop<TRow extends Row>({
  activeRowId,
  activeLaneId,
  target,
  groups,
  rankField,
  rankForRow,
}: {
  activeRowId: string;
  activeLaneId: string;
  target: BoardDropTarget;
  groups: readonly RowGroup<TRow>[];
  rankField?: string;
  rankForRow: (row: TableRowModel<TRow>) => number | undefined;
}): BoardDropMove | null {
  if (target.rowId === activeRowId) return null;
  if (!rankField) {
    return target.laneId === activeLaneId ? null : { laneId: target.laneId };
  }

  const targetGroup = groups.find((group) => group.key === target.laneId);
  if (!targetGroup) return null;
  const currentRows = targetGroup.rows;
  const remainingRows = currentRows.filter((row) => row.id !== activeRowId);
  const overIndex = target.rowId
    ? currentRows.findIndex((row) => row.id === target.rowId)
    : remainingRows.length;
  const insertIndex = overIndex < 0
    ? remainingRows.length
    : Math.min(overIndex, remainingRows.length);

  if (target.laneId === activeLaneId) {
    const currentOrder = currentRows.map((row) => row.id);
    const nextOrder = remainingRows.map((row) => row.id);
    nextOrder.splice(insertIndex, 0, activeRowId);
    if (sameOrder(currentOrder, nextOrder)) return null;
  }

  return {
    laneId: target.laneId,
    rank: fractionalRankBetween(
      remainingRows[insertIndex - 1]
        ? rankForRow(remainingRows[insertIndex - 1]!)
        : undefined,
      remainingRows[insertIndex]
        ? rankForRow(remainingRows[insertIndex]!)
        : undefined,
    ),
  };
}

/** Requested append rank for a card created at the end of one lane. */
export function boardAppendRank<TRow extends Row>(
  group: RowGroup<TRow>,
  rankForRow: (row: TableRowModel<TRow>) => number | undefined,
): number {
  return fractionalRankBetween(
    group.rows.length > 0 ? rankForRow(group.rows[group.rows.length - 1]!) : undefined,
    undefined,
  );
}

function sameOrder(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length
    && left.every((id, index) => id === right[index]);
}

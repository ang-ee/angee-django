import * as React from "react";
import type { Virtualizer } from "@tanstack/react-virtual";
import { TableCell, TableRow } from "../../../ui/table";
export function VirtualPaddingRow({
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

/**
 * The padding-row windowing math shared by every virtualized list body: the
 * leading/trailing spacer heights plus the indexes of the items to render. One
 * owner so the flat and grouped bodies window identically. `estimateSize` feeds
 * the pre-measure fallback (before the virtualizer has real rects), keyed per the
 * caller's item kinds.
 */
export function useVirtualWindow(
  rowVirtualizer: Virtualizer<HTMLDivElement, Element>,
  itemCount: number,
  estimateSize: (index: number) => number,
): { paddingTop: number; paddingBottom: number; visibleIndexes: number[] } {
  const virtualItems = rowVirtualizer.getVirtualItems();
  const visibleIndexes =
    virtualItems.length > 0
      ? virtualItems.map((item) => item.index)
      : Array.from({ length: Math.min(itemCount, 20) }, (_, index) => index);
  const firstVirtualItem = virtualItems[0];
  const lastVirtualItem = virtualItems[virtualItems.length - 1];
  const paddingTop = firstVirtualItem?.start ?? 0;
  let fallbackTail = 0;
  if (virtualItems.length === 0) {
    for (let index = visibleIndexes.length; index < itemCount; index += 1) {
      fallbackTail += estimateSize(index);
    }
  }
  const paddingBottom = Math.max(
    0,
    virtualItems.length > 0
      ? rowVirtualizer.getTotalSize() - (lastVirtualItem?.end ?? 0)
      : fallbackTail,
  );
  return { paddingTop, paddingBottom, visibleIndexes };
}

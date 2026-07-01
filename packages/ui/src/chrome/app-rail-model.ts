export type RailDropPlacement = "before" | "after";

export interface RailOrderItem {
  id: string;
}

export interface RailTargetItem {
  id?: string;
  target?: string;
}

export function orderedRailItems<TItem extends RailOrderItem>(
  items: readonly TItem[],
  order: readonly string[] | null | undefined,
): readonly TItem[] {
  if (!order?.length) return items;
  const byId = new Map(items.map((item) => [item.id, item]));
  const seen = new Set<string>();
  const ordered: TItem[] = [];

  for (const id of order) {
    if (seen.has(id)) continue;
    const item = byId.get(id);
    if (!item) continue;
    seen.add(id);
    ordered.push(item);
  }

  for (const item of items) {
    if (seen.has(item.id)) continue;
    ordered.push(item);
  }

  return ordered;
}

export function moveRailItem(
  order: readonly string[],
  draggedId: string,
  targetId: string,
  placement: RailDropPlacement,
): readonly string[] {
  if (draggedId === targetId) return order;
  if (!order.includes(draggedId) || !order.includes(targetId)) return order;

  const next = order.filter((id) => id !== draggedId);
  const targetIndex = next.indexOf(targetId);
  const insertIndex = placement === "after" ? targetIndex + 1 : targetIndex;
  next.splice(insertIndex, 0, draggedId);
  return next;
}

export function railSortableMove(
  order: readonly string[],
  draggedId: string,
  overId: string,
): readonly string[] {
  if (draggedId === overId) return order;
  const draggedIndex = order.indexOf(draggedId);
  const overIndex = order.indexOf(overId);
  if (draggedIndex === -1 || overIndex === -1) return order;
  return moveRailItem(
    order,
    draggedId,
    overId,
    draggedIndex < overIndex ? "after" : "before",
  );
}

export function railDefaultTarget(
  item: Pick<RailTargetItem, "target">,
): string | null {
  const target = item.target?.trim() ?? "";
  if (!target || target === "#") return null;
  return target;
}

export function railItemIdForTarget<TItem extends RailTargetItem>(
  items: readonly TItem[],
  target: string | null | undefined,
): string | null {
  if (!target) return null;
  return items.find((item) => item.id && railDefaultTarget(item) === target)?.id ?? null;
}

export function sameRailOrder(
  a: readonly string[],
  b: readonly string[],
): boolean {
  return a.length === b.length && a.every((id, index) => id === b[index]);
}

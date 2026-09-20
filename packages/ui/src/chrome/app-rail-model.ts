import type { MouseEvent } from "react";

export type RailDropPlacement = "before" | "after";

export interface RailLinkToggleProps {
  "aria-expanded"?: boolean;
  "aria-haspopup"?: "dialog";
  onClick?: (event: MouseEvent<HTMLElement>) => void;
}

/**
 * Rail links open temporary navigation when supplied; otherwise only the
 * current page's link toggles desktop expansion. Modified/non-primary clicks
 * keep native link behavior in either presentation.
 */
export function railLinkToggleProps(
  target: string | undefined,
  pathname: string,
  toggle: (() => void) | undefined,
  expanded: boolean,
  openNavigation?: ((target: string) => void) | undefined,
): RailLinkToggleProps {
  if (!target) return {};
  const activate = openNavigation
    ? () => openNavigation(target)
    : target === pathname ? toggle : undefined;
  if (!activate) return {};
  return {
    ...(openNavigation
      ? { "aria-haspopup": "dialog" as const }
      : { "aria-expanded": expanded }),
    onClick: (event) => {
      if (event.defaultPrevented) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
        return;
      }
      if (event.button !== 0) return;
      event.preventDefault();
      activate();
    },
  };
}

export interface RailOrderItem {
  id: string;
}

export interface RailTargetItem {
  id?: string;
  target?: string;
}

/** Resolve the persisted rail preference at the viewport where expansion exists. */
export function resolvedRailExpanded(
  preference: boolean | undefined,
  largeViewport: boolean,
): boolean {
  return largeViewport && (preference ?? true);
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

export function sameRailOrder(
  a: readonly string[],
  b: readonly string[],
): boolean {
  return a.length === b.length && a.every((id, index) => id === b[index]);
}

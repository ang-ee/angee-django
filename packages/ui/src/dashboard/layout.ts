import { DASHBOARD_LIMITS, type WidgetSpec } from "./headless";

export interface DashboardRect {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

function packingOrder<T extends DashboardRect>(rects: readonly T[]): T[] {
  return [...rects].sort((left, right) =>
    left.y - right.y || left.x - right.x || left.id.localeCompare(right.id),
  );
}

function overlaps(left: DashboardRect, right: DashboardRect): boolean {
  return left.x < right.x + right.w
    && right.x < left.x + left.w
    && left.y < right.y + right.h
    && right.y < left.y + left.h;
}

export function dashboardRectFits(
  rect: DashboardRect,
  placed: readonly DashboardRect[],
  columns: number,
): boolean {
  return rect.x >= 0
    && rect.y >= 0
    && rect.x + rect.w <= columns
    && !placed.some((other) => other.id !== rect.id && overlaps(rect, other));
}

export function firstDashboardSlot(
  rects: readonly DashboardRect[],
  size: { w: number; h: number },
  columns: number,
): { x: number; y: number } {
  const w = Math.max(1, Math.min(size.w, columns));
  const h = Math.max(1, size.h);
  const bottom = rects.reduce((max, rect) => Math.max(max, rect.y + rect.h), 0);
  for (let y = 0; y <= bottom; y += 1) {
    for (let x = 0; x + w <= columns; x += 1) {
      if (dashboardRectFits({ id: "__probe__", x, y, w, h }, rects, columns)) {
        return { x, y };
      }
    }
  }
  return { x: 0, y: bottom };
}

/** Canonical persisted packing: stable first-fit with no active-layout holes. */
export function packDashboardLayout<T extends DashboardRect>(
  rects: readonly T[],
  columns: number,
): T[] {
  const placed: DashboardRect[] = [];
  const packed = new Map<string, DashboardRect>();
  for (const rect of packingOrder(rects)) {
    const w = Math.max(1, Math.min(rect.w, columns));
    const slot = firstDashboardSlot(placed, { w, h: rect.h }, columns);
    const next = { ...rect, ...slot, w };
    placed.push(next);
    packed.set(rect.id, next);
  }
  return rects.map((rect) => ({ ...rect, ...(packed.get(rect.id) ?? rect) }));
}

export function moveDashboardRect<T extends DashboardRect>(
  rects: readonly T[],
  id: string,
  to: { x: number; y: number },
  columns: number,
): T[] {
  const moved = rects.find((rect) => rect.id === id);
  if (!moved) return [...rects];
  const w = Math.max(1, Math.min(moved.w, columns));
  const target = {
    ...moved,
    w,
    x: Math.max(0, Math.min(to.x, columns - w)),
    y: Math.max(0, to.y),
  };
  return packDashboardLayout(
    rects.map((rect) => rect.id === id
      ? target
      : overlaps(rect, target)
        ? { ...rect, y: target.y + target.h }
        : rect),
    columns,
  );
}

export function resizeDashboardRect<T extends DashboardRect>(
  rects: readonly T[],
  id: string,
  size: { w: number; h: number },
  columns: number,
): T[] {
  return packDashboardLayout(
    rects.map((rect) => rect.id === id
      ? {
          ...rect,
          w: Math.max(1, Math.min(size.w, columns - rect.x)),
          h: Math.max(1, Math.min(size.h, DASHBOARD_LIMITS.widgetHeight)),
        }
      : rect),
    columns,
  );
}

/** Read-only responsive projection; it never becomes persistence input. */
export function projectDashboardLayout(
  widgets: readonly WidgetSpec[],
  columns: number,
  sourceColumns = columns,
): WidgetSpec[] {
  const scale = columns / Math.max(1, sourceColumns);
  return packDashboardLayout(
    widgets.filter((widget) => !widget.isArchived).map((widget) => ({
      ...widget,
      w: Math.max(1, Math.min(Math.round(widget.w * scale), columns)),
      x: Math.max(0, Math.min(Math.floor(widget.x * scale), columns - 1)),
    })),
    columns,
  );
}

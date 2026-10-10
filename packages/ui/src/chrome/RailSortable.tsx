import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  DndContext,
  closestCenter,
  type DragAbortEvent,
  type DragEndEvent,
  type DragPendingEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { useDndKitSensors } from "../lib/dnd";
import { StatusDot } from "../ui/status-icon";
import { moveRailItem, railSortableMove, railTooltip, sameRailOrder } from "./app-rail-model";
import type { ChromeMenuNode } from "./menu-tree";

const LONG_PRESS_MS = 650;

/** The rail's persisted order and default app, and how its owner writes them. */
export interface RailSorting {
  /** The default app; `null` when no row is. */
  defaultItemId: string | null;
  onOrderChange: (order: readonly string[]) => void;
  onItemLongPress: (item: ChromeMenuNode) => void;
}

interface RailSortableRows {
  defaultItemId: string | null;
  moveByKey: (item: ChromeMenuNode, event: KeyboardEvent<HTMLElement>) => void;
}

const RailSortableContext = createContext<RailSortableRows | null>(null);

/**
 * The one owner of reordering rail rows and choosing the default app, shared
 * by the icon rail and the expanded tree's app roots. Dragging a row's link
 * moves the row, Alt+ArrowUp/Down moves it by keyboard, and a long press makes
 * it the default app. dnd-kit's pointer sensor reports the press; a drag or a
 * long press swallows the click its release dispatches, so it neither
 * navigates nor toggles. Rows compose `useRailSortableItem`.
 */
export function RailSortable({
  items,
  defaultItemId,
  onOrderChange,
  onItemLongPress,
  children,
}: RailSorting & {
  /** The sortable rows, in display order. */
  items: readonly ChromeMenuNode[];
  children: ReactNode;
}): ReactElement {
  const sensors = useDndKitSensors(6);
  const order = useMemo(() => items.map((item) => item.id), [items]);
  const press = useRef<{ id: string; longPressed: boolean; timer: ReturnType<typeof setTimeout> } | null>(null);
  // A drag that starts after a long press only finishes that press.
  const dragBlocked = useRef(false);

  const endPress = useCallback(() => {
    if (press.current) globalThis.clearTimeout(press.current.timer);
    press.current = null;
  }, []);
  useEffect(() => endPress, [endPress]);

  const commit = useCallback((next: readonly string[]) => {
    if (!sameRailOrder(next, order)) onOrderChange(next);
  }, [onOrderChange, order]);
  const rows = useMemo<RailSortableRows>(() => ({
    defaultItemId,
    moveByKey: (item, event) => {
      if (!event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
      const up = event.key === "ArrowUp";
      const index = order.indexOf(item.id);
      const targetId = order[up ? index - 1 : index + 1];
      if (!targetId) return;
      event.preventDefault();
      commit(moveRailItem(order, item.id, targetId, up ? "before" : "after"));
    },
  }), [commit, defaultItemId, order]);

  const handleDragPending = ({ id, offset }: DragPendingEvent) => {
    const item = items.find((row) => row.id === id);
    // Later pending events report pointer moves within the same press.
    if (offset || !item) return;
    endPress();
    const current = {
      id: item.id,
      longPressed: false,
      timer: globalThis.setTimeout(() => {
        current.longPressed = true;
        onItemLongPress(item);
      }, LONG_PRESS_MS),
    };
    press.current = current;
  };
  const handleDragAbort = ({ id }: DragAbortEvent) => {
    if (press.current?.id !== id) return;
    if (press.current.longPressed) swallowTrailingClick();
    endPress();
  };

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragPending={handleDragPending}
      onDragAbort={handleDragAbort}
      onDragStart={() => {
        dragBlocked.current = press.current?.longPressed ?? false;
        endPress();
      }}
      onDragEnd={({ active, over }: DragEndEvent) => {
        swallowTrailingClick();
        const blocked = dragBlocked.current;
        dragBlocked.current = false;
        if (!blocked && over) commit(railSortableMove(order, String(active.id), String(over.id)));
      }}
      onDragCancel={() => {
        dragBlocked.current = false;
        swallowTrailingClick();
      }}
    >
      <SortableContext items={order} strategy={verticalListSortingStrategy}>
        <RailSortableContext.Provider value={rows}>{children}</RailSortableContext.Provider>
      </SortableContext>
    </DndContext>
  );
}

export interface RailSortableItem {
  /** Spread on the element that moves — the whole row — merging `className`. */
  node: { ref: (element: HTMLElement | null) => void; style: CSSProperties; className: string };
  /** Spread on the row's link after its own props, merging `className`. */
  link: {
    className: string;
    draggable: false;
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
    onPointerDown: (event: PointerEvent<HTMLElement>) => void;
  };
  defaultApp: boolean;
  /** The gesture hint, followed by the developer description. */
  tooltip: string | undefined;
}

/**
 * One row of the enclosing `RailSortable`. dnd-kit's keyboard drag and its
 * screen-reader instructions stay unbound: Alt+Arrow is the keyboard path, and
 * the link keeps its own role.
 */
export function useRailSortableItem(item: ChromeMenuNode, label: string, description?: string): RailSortableItem {
  const t = useUiT();
  const rows = useContext(RailSortableContext);
  if (!rows) throw new Error("A sortable rail row rendered outside RailSortable.");
  const { setNodeRef, transform, transition, isDragging, listeners } = useSortable({ id: item.id });
  const defaultApp = rows.defaultItemId === item.id;
  return {
    node: {
      ref: setNodeRef,
      style: {
        transform: transform
          ? `translate3d(${Math.round(transform.x)}px, ${Math.round(transform.y)}px, 0) scaleX(${transform.scaleX}) scaleY(${transform.scaleY})`
          : undefined,
        transition,
      },
      className: cn(
        "will-change-transform",
        isDragging && "relative z-10 scale-[1.02] rounded-6 bg-rail opacity-95 shadow-lg ring-1 ring-brand/50",
      ),
    },
    link: {
      className: "cursor-grab select-none touch-none active:cursor-grabbing",
      draggable: false,
      onKeyDown: (event) => rows.moveByKey(item, event),
      onPointerDown: (event) => listeners?.onPointerDown?.(event),
    },
    defaultApp,
    tooltip: railTooltip(
      false,
      t(defaultApp ? "chrome.defaultRailItemHint" : "chrome.railItemHint", { label }),
      description,
    ),
  };
}

/** The default app's mark: a dot on its row's glyph or button. */
export function RailDefaultMark({ className }: { className: string }): ReactElement {
  return (
    <StatusDot className={cn("absolute border border-rail", className)} size="sm" tone="success" />
  );
}

/**
 * Swallow the click a pointer release dispatches next. dnd-kit only stops a
 * dragged click's propagation, which leaves a link's native navigation.
 */
function swallowTrailingClick(): void {
  const swallow = (event: Event) => {
    event.preventDefault();
    event.stopPropagation();
  };
  window.addEventListener("click", swallow, { capture: true, once: true });
  globalThis.setTimeout(() => window.removeEventListener("click", swallow, { capture: true }), 0);
}

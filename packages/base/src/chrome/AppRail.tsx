import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactElement,
} from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { useMenus } from "@angee/sdk";

import { useBaseT } from "../i18n";
import { cn } from "../lib/cn";
import { Tooltip } from "../ui/tooltip";
import { AppChooser } from "./AppChooser";
import { Glyph } from "./Glyph";
import {
  type ChromeMenuItem,
  type ChromeMenuNode,
  MenuTree,
} from "./menu-tree";
import {
  moveRailItem,
  orderedRailItems,
  railSortableMove,
  sameRailOrder,
  type RailDropPlacement,
} from "./app-rail-model";
import { useAppRailPreferences } from "./app-rail-preferences";

export interface AppRailProps {
  className?: string;
}

const RAIL_BUTTON =
  "group relative grid size-9 place-content-center rounded-6 text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring";
const RAIL_BUTTON_ACTIVE =
  "bg-rail-hi text-on-rail-hi before:absolute before:-left-[7px] before:top-1/2 before:h-[18px] before:w-[3px] before:-translate-y-1/2 before:rounded-r-2 before:bg-brand before:content-['']";

export function AppRail({ className }: AppRailProps): ReactElement {
  const t = useBaseT();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const menus = useMenus() as readonly ChromeMenuItem[];
  const tree = useMemo(() => MenuTree.from(menus), [menus]);
  const { railPreferences, setRailPreferences } = useAppRailPreferences();
  const items = useMemo(
    () => orderedRailItems(tree.railMenuItems(), railPreferences.order),
    [railPreferences.order, tree],
  );
  // The rail has two zones: domain apps on top (draggable), platform apps
  // clustered at the bottom — the same `group` taxonomy the AppChooser splits
  // into "Apps"/"Platform". Each zone is its own DnD context, so apps reorder
  // within a zone but never cross the divider. The single `order` pref stays the
  // source of truth, stored domain-first then platform.
  const domainItems = useMemo(
    () => items.filter((item) => item.group !== "platform"),
    [items],
  );
  const platformItems = useMemo(
    () => items.filter((item) => item.group === "platform"),
    [items],
  );
  const domainIds = useMemo(() => domainItems.map((item) => item.id), [domainItems]);
  const platformIds = useMemo(
    () => platformItems.map((item) => item.id),
    [platformItems],
  );
  const itemIds = useMemo(() => items.map((item) => item.id), [items]);
  const defaultItemId = itemIds.includes(railPreferences.defaultItemId ?? "")
    ? railPreferences.defaultItemId
    : null;
  const commitZoneOrder = useCallback(
    (nextDomain: readonly string[], nextPlatform: readonly string[]) => {
      setRailPreferences({
        ...railPreferences,
        order: [...nextDomain, ...nextPlatform],
      });
    },
    [railPreferences, setRailPreferences],
  );
  const handleDomainOrderChange = useCallback(
    (order: readonly string[]) => commitZoneOrder(order, platformIds),
    [commitZoneOrder, platformIds],
  );
  const handlePlatformOrderChange = useCallback(
    (order: readonly string[]) => commitZoneOrder(domainIds, order),
    [commitZoneOrder, domainIds],
  );
  const handleItemLongPress = useCallback(
    (item: ChromeMenuNode) => {
      if (!item.target || item.id === defaultItemId) return;
      setRailPreferences({
        ...railPreferences,
        defaultItemId: item.id,
      });
    },
    [defaultItemId, railPreferences, setRailPreferences],
  );

  return (
    <aside
      className={cn(
        "area-rail z-rail flex h-full w-rail-w flex-col items-center gap-2 border-r border-border-on-rail bg-rail py-2 text-on-rail",
        className,
      )}
    >
      <AppChooser className="text-on-rail-hi" />
      <div className="h-px w-6 bg-border-on-rail" />
      <nav
        aria-label={t("chrome.primaryNav")}
        className="flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto"
      >
        <SortableRail
          items={domainItems}
          pathname={pathname}
          defaultItemId={defaultItemId}
          onItemLongPress={handleItemLongPress}
          onOrderChange={handleDomainOrderChange}
        />
        {platformItems.length ? (
          <>
            <div className="flex-1" aria-hidden />
            <div className="h-px w-6 shrink-0 bg-border-on-rail" aria-hidden />
            <SortableRail
              items={platformItems}
              pathname={pathname}
              defaultItemId={defaultItemId}
              onItemLongPress={handleItemLongPress}
              onOrderChange={handlePlatformOrderChange}
            />
          </>
        ) : null}
      </nav>
    </aside>
  );
}

function SortableRail({
  items,
  pathname,
  defaultItemId,
  onItemLongPress,
  onOrderChange,
}: {
  items: readonly ChromeMenuNode[];
  pathname: string;
  defaultItemId: string | null;
  onItemLongPress: (item: ChromeMenuNode) => void;
  onOrderChange: (order: readonly string[]) => void;
}): ReactElement {
  const [draftOrder, setDraftOrder] = useState<readonly string[] | null>(null);
  const [activeDragId, setActiveDragId] = useState<string | null>(null);
  const longPressRef = useRef<{
    id: string;
    pointerId: number;
    longPressed: boolean;
    longPressTimer?: ReturnType<typeof globalThis.setTimeout>;
  } | null>(null);
  const blockedDragRef = useRef<string | null>(null);
  const suppressClickRef = useRef(false);
  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: { distance: 6 },
    }),
  );
  const railItems = useMemo(
    () => orderedRailItems(items, draftOrder),
    [draftOrder, items],
  );
  const railOrder = useMemo(
    () => railItems.map((item) => item.id),
    [railItems],
  );

  useEffect(() => {
    if (activeDragId) return;
    setDraftOrder(null);
  }, [activeDragId, items]);

  const commitOrder = useCallback(
    (next: readonly string[]) => {
      setDraftOrder(next);
      onOrderChange(next);
    },
    [onOrderChange],
  );

  const commitMove = useCallback(
    (draggedId: string, targetId: string, placement: RailDropPlacement) => {
      const next = moveRailItem(railOrder, draggedId, targetId, placement);
      if (next === railOrder || sameRailOrder(next, railOrder)) return;
      commitOrder(next);
    },
    [commitOrder, railOrder],
  );

  const clearLongPressTimer = useCallback(() => {
    const timer = longPressRef.current?.longPressTimer;
    if (!timer) return;
    globalThis.clearTimeout(timer);
    longPressRef.current!.longPressTimer = undefined;
  }, []);

  useEffect(() => () => clearLongPressTimer(), [clearLongPressTimer]);

  const suppressNextClick = useCallback(() => {
    suppressClickRef.current = true;
    globalThis.setTimeout(() => {
      suppressClickRef.current = false;
    }, 0);
  }, []);

  const beginLongPress = useCallback(
    (item: ChromeMenuNode, event: PointerEvent<HTMLElement>) => {
      if (event.button !== 0) return;
      const pointerId = event.pointerId;
      clearLongPressTimer();
      const longPressTimer = globalThis.setTimeout(() => {
        const current = longPressRef.current;
        if (
          !current
          || current.id !== item.id
          || current.pointerId !== pointerId
        ) return;
        current.longPressed = true;
        suppressNextClick();
        onItemLongPress(item);
      }, 650);
      longPressRef.current = {
        id: item.id,
        pointerId,
        longPressed: false,
        longPressTimer,
      };
    },
    [clearLongPressTimer, onItemLongPress, suppressNextClick],
  );

  const endLongPress = useCallback(
    (item: ChromeMenuNode, event: PointerEvent<HTMLElement>) => {
      const current = longPressRef.current;
      if (
        !current
        || current.id !== item.id
        || current.pointerId !== event.pointerId
      ) return;
      clearLongPressTimer();
      if (current.longPressed) {
        event.preventDefault();
        suppressNextClick();
      }
      longPressRef.current = null;
    },
    [clearLongPressTimer, suppressNextClick],
  );

  const cancelLongPress = useCallback(
    (item: ChromeMenuNode, event: PointerEvent<HTMLElement>) => {
      const current = longPressRef.current;
      if (
        !current
        || current.id !== item.id
        || current.pointerId !== event.pointerId
      ) return;
      clearLongPressTimer();
      longPressRef.current = null;
    },
    [clearLongPressTimer],
  );

  const handleDragStart = useCallback(
    ({ active }: DragStartEvent) => {
      const activeId = String(active.id);
      const current = longPressRef.current;
      if (current?.id === activeId && current.longPressed) {
        blockedDragRef.current = activeId;
      }
      clearLongPressTimer();
      longPressRef.current = null;
      setActiveDragId(activeId);
    },
    [clearLongPressTimer],
  );

  const handleDragEnd = useCallback(
    ({ active, over }: DragEndEvent) => {
      setActiveDragId(null);
      clearLongPressTimer();
      longPressRef.current = null;
      const draggedId = String(active.id);
      const blockedDrag = blockedDragRef.current === draggedId;
      blockedDragRef.current = null;
      if (blockedDrag) {
        suppressNextClick();
        return;
      }
      const overId = over ? String(over.id) : null;
      if (!overId || draggedId === overId) return;
      const next = railSortableMove(railOrder, draggedId, overId);
      if (next !== railOrder && !sameRailOrder(next, railOrder)) {
        commitOrder(next);
      }
      suppressNextClick();
    },
    [clearLongPressTimer, commitOrder, railOrder, suppressNextClick],
  );

  const handleDragCancel = useCallback(() => {
    setActiveDragId(null);
    blockedDragRef.current = null;
    clearLongPressTimer();
    longPressRef.current = null;
  }, [clearLongPressTimer]);

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
    >
      <SortableContext items={railOrder} strategy={verticalListSortingStrategy}>
        {railItems.map((item) => (
          <RailItem
            key={item.id}
            item={item}
            active={item.isActive(pathname)}
            defaultApp={defaultItemId === item.id}
            dragging={activeDragId === item.id}
            onLongPressStart={beginLongPress}
            onLongPressEnd={endLongPress}
            onLongPressCancel={cancelLongPress}
            onKeyboardMove={(event) => {
              if (!event.altKey) return;
              if (event.key !== "ArrowUp" && event.key !== "ArrowDown") {
                return;
              }
              const index = railOrder.indexOf(item.id);
              const targetId = event.key === "ArrowUp"
                ? railOrder[index - 1]
                : railOrder[index + 1];
              if (!targetId) return;
              event.preventDefault();
              commitMove(
                item.id,
                targetId,
                event.key === "ArrowUp" ? "before" : "after",
              );
            }}
            onClick={(event) => {
              if (suppressClickRef.current) event.preventDefault();
            }}
          />
        ))}
      </SortableContext>
    </DndContext>
  );
}

function RailItem({
  item,
  active,
  defaultApp,
  dragging,
  onLongPressStart,
  onLongPressEnd,
  onLongPressCancel,
  onKeyboardMove,
  onClick,
}: {
  item: ChromeMenuNode;
  active: boolean;
  defaultApp: boolean;
  dragging: boolean;
  onLongPressStart: (
    item: ChromeMenuNode,
    event: PointerEvent<HTMLElement>,
  ) => void;
  onLongPressEnd: (
    item: ChromeMenuNode,
    event: PointerEvent<HTMLElement>,
  ) => void;
  onLongPressCancel: (
    item: ChromeMenuNode,
    event: PointerEvent<HTMLElement>,
  ) => void;
  onKeyboardMove: (event: KeyboardEvent<HTMLElement>) => void;
  onClick: (event: MouseEvent<HTMLElement>) => void;
}): ReactElement | null {
  const iconName = item.iconName;
  const to = item.target;
  const label = item.displayLabel;
  const title = railItemTitle(label, defaultApp);
  const {
    attributes,
    isDragging,
    listeners,
    setNodeRef,
    transform,
    transition,
  } = useSortable({
    id: item.id,
    data: {
      type: "app-rail-item",
      itemId: item.id,
    },
  });
  // Rail items always carry a target (railMenuItems filters target-less nodes);
  // guard after the hooks so hook order stays stable (Rules of Hooks).
  if (!to) return null;
  const { role: _dragRole, ...dragAttributes } = attributes;
  const activeDrag = dragging || isDragging;
  return (
    <Tooltip label={title} side="right">
      <Link
        ref={setNodeRef}
        to={to}
        aria-label={label}
        aria-current={active ? "page" : undefined}
        aria-grabbed={activeDrag}
        draggable={false}
        onPointerDown={(event) => {
          listeners?.onPointerDown?.(event);
          onLongPressStart(item, event);
        }}
        onPointerUp={(event) => onLongPressEnd(item, event)}
        onPointerCancel={(event) => onLongPressCancel(item, event)}
        onKeyDown={onKeyboardMove}
        onClick={onClick}
        style={sortableRailTransformStyle(transform, transition)}
        className={cn(
          RAIL_BUTTON,
          active && RAIL_BUTTON_ACTIVE,
          "cursor-grab select-none touch-none will-change-transform transition-[transform,background-color,color,box-shadow,opacity] duration-150 ease-out active:cursor-grabbing",
          activeDrag && "z-10 scale-[1.12] opacity-95 shadow-lg ring-1 ring-brand/50",
        )}
        {...dragAttributes}
      >
        <Glyph name={iconName} fallbackName="help" size={16} />
        <span className="sr-only">{label}</span>
        {defaultApp ? (
          <span
            aria-hidden
            className="absolute bottom-1 right-1 size-1.5 rounded-full border border-rail bg-success-text"
          />
        ) : null}
      </Link>
    </Tooltip>
  );
}

function railItemTitle(label: string, defaultApp: boolean): string {
  return [
    label,
    defaultApp ? "default app" : null,
    "drag to reorder",
    "long press to set default",
  ].filter(Boolean).join(" - ");
}

function sortableRailTransformStyle(
  transform: {
    x: number;
    y: number;
    scaleX: number;
    scaleY: number;
  } | null,
  transition: string | undefined,
): CSSProperties {
  return {
    transform: transform
      ? `translate3d(${Math.round(transform.x)}px, ${Math.round(transform.y)}px, 0) scaleX(${transform.scaleX}) scaleY(${transform.scaleY})`
      : undefined,
    transition,
  };
}

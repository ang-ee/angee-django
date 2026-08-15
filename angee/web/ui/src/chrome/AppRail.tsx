import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactElement,
  type Ref,
} from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import {
  DndContext,
  closestCenter,
  type DragEndEvent,
  type DragStartEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { useDndKitSensors } from "../lib/dnd";
import { toneGlyph } from "../lib/tones";
import { LARGE_VIEWPORT_QUERY, useMediaQuery } from "../lib/use-media-query";
import { Button } from "../ui/button";
import { Tooltip } from "../ui/tooltip";
import { AppChooser } from "./AppChooser";
import { AppRailTree, appRailTreeVariants } from "./AppRailTree";
import { Glyph } from "./Glyph";
import {
  MenuTree,
  type ChromeMenuItem,
  type ChromeMenuNode,
} from "./menu-tree";
import { useChromeMenuTree } from "./refine-menu";
import {
  activeLinkToggleProps,
  moveRailItem,
  orderedRailItems,
  railSortableMove,
  resolvedRailExpanded,
  sameRailOrder,
  type RailDropPlacement,
} from "./app-rail-model";
import { useAppRailPreferences } from "./app-rail-preferences";

export interface AppRailProps {
  className?: string;
  /** Menu declarations to project instead of the composed runtime menu. */
  menuItems?: readonly ChromeMenuItem[];
  /** Publishes the rail's current width so the shell grid can track it. */
  onWidthChange?: (width: string | null) => void;
}

export const APP_RAIL_COLLAPSED_WIDTH = "var(--spacing-rail-w)";
export const APP_RAIL_EXPANDED_WIDTH = "var(--spacing-rail-expanded-w)";

const RAIL_BUTTON =
  "group relative grid size-9 place-content-center rounded-6 text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring";
const RAIL_BUTTON_ACTIVE =
  "bg-rail-hi text-on-rail-hi before:absolute before:-left-[7px] before:top-1/2 before:h-[18px] before:w-[3px] before:-translate-y-1/2 before:rounded-r-2 before:bg-brand before:content-['']";

/**
 * The global app rail: compact icons or one in-place accordion navigation
 * tree. Clicking the active app (or Settings) a second time toggles the
 * expansion; the expansion toggle itself sits pinned at the rail's foot,
 * outside the scrolling list.
 */
export function AppRail({
  className,
  menuItems,
  onWidthChange,
}: AppRailProps): ReactElement {
  const t = useUiT();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const runtimeTree = useChromeMenuTree();
  const tree = useMemo(
    () => menuItems ? MenuTree.from(menuItems) : runtimeTree,
    [menuItems, runtimeTree],
  );
  const { railPreferences, setRailPreferences } = useAppRailPreferences();
  const largeViewport = useMediaQuery(LARGE_VIEWPORT_QUERY);
  const expanded = resolvedRailExpanded(
    railPreferences.expanded,
    largeViewport,
  );
  const place = tree.railPlace(pathname);
  const settingsActive = place.scope === "settings";
  const items = useMemo(
    () => orderedRailItems(tree.railMenuItems(), railPreferences.order),
    [railPreferences.order, tree],
  );
  const settings = tree.settingsEntry();
  const activeRootId = settingsActive
    ? undefined
    : place.activeRootId ?? undefined;
  const itemIds = useMemo(() => items.map((item) => item.id), [items]);
  const defaultItemId = itemIds.includes(railPreferences.defaultItemId ?? "")
    ? railPreferences.defaultItemId
    : null;
  const handleOrderChange = useCallback(
    (order: readonly string[]) => {
      setRailPreferences({ ...railPreferences, order });
    },
    [railPreferences, setRailPreferences],
  );
  const handleItemLongPress = useCallback(
    (item: ChromeMenuNode) => {
      if (!item.target || item.id === defaultItemId) return;
      setRailPreferences({ ...railPreferences, defaultItemId: item.id });
    },
    [defaultItemId, railPreferences, setRailPreferences],
  );
  const toggleExpanded = useCallback(() => {
    setRailPreferences({ ...railPreferences, expanded: !expanded });
  }, [expanded, railPreferences, setRailPreferences]);
  const footerToggleRef = useRef<HTMLButtonElement | null>(null);
  const focusFooterToggle = useRef(false);
  // A link-driven toggle unmounts the clicked link with the mode swap, so it
  // hands focus to the footer toggle — the one control both modes share.
  const toggleFromActiveLink = useCallback(() => {
    focusFooterToggle.current = true;
    toggleExpanded();
  }, [toggleExpanded]);
  const onActiveToggle = largeViewport ? toggleFromActiveLink : undefined;
  const width = expanded ? APP_RAIL_EXPANDED_WIDTH : APP_RAIL_COLLAPSED_WIDTH;
  const navId = useId();

  // The width and the grid's --rail-current-w are one layout fact — publish
  // before paint so both land in the same frame.
  useLayoutEffect(() => {
    onWidthChange?.(width);
  }, [onWidthChange, width]);
  useLayoutEffect(() => () => onWidthChange?.(null), [onWidthChange]);
  useLayoutEffect(() => {
    if (!focusFooterToggle.current) return;
    focusFooterToggle.current = false;
    footerToggleRef.current?.focus();
  }, [expanded]);

  return (
    <aside
      style={{ width }}
      className={cn(
        // Sticky + h-dvh pin the rail (and its footer toggle) to the viewport
        // even when the document scrolls a tall page.
        "area-rail z-rail sticky top-0 flex h-dvh shrink-0 flex-col gap-2 overflow-hidden border-r border-border-on-rail bg-rail py-2 text-on-rail",
        className,
      )}
    >
      <div
        className={cn(
          "flex shrink-0 items-center",
          expanded ? "gap-2 px-2" : "justify-center",
        )}
      >
        <AppChooser
          menuItems={tree}
          className="shrink-0 text-on-rail-hi"
        />
        {expanded ? (
          <span className="min-w-0 truncate text-13 font-semibold text-on-rail-hi">
            {t("chrome.apps")}
          </span>
        ) : null}
      </div>
      <nav
        id={navId}
        aria-label={t("chrome.primaryNav")}
        data-rail-list="true"
        className="min-h-0 w-full flex-1 overflow-y-auto overscroll-contain"
      >
        {expanded ? (
          <AppRailTree
            scope={place.scope}
            roots={settingsActive ? place.roots : items}
            activeRootId={place.activeRootId}
            onActiveToggle={onActiveToggle}
          />
        ) : (
          <SortableRail
            items={items}
            activeRootId={activeRootId}
            defaultItemId={defaultItemId}
            expanded={expanded}
            pathname={pathname}
            onActiveToggle={onActiveToggle}
            onItemLongPress={handleItemLongPress}
            onOrderChange={handleOrderChange}
          />
        )}
        {settings ? (
          <>
            <div className={cn(
              "my-2 h-px bg-border-on-rail",
              expanded ? "mx-2" : "mx-auto w-6",
            )} aria-hidden="true" />
            <RailSettingsItem
              active={settingsActive}
              expanded={expanded}
              icon={settings.icon}
              label={t("chrome.settings")}
              to={settings.target}
              pathname={pathname}
              onActiveToggle={onActiveToggle}
            />
          </>
        ) : null}
      </nav>
      {largeViewport ? (
        <div
          className={cn(
            "flex shrink-0 border-t border-border-on-rail pt-2",
            expanded ? "px-2" : "justify-center",
          )}
        >
          <RailExpansionToggle
            buttonRef={footerToggleRef}
            controls={navId}
            expanded={expanded}
            onToggle={toggleExpanded}
          />
        </div>
      ) : null}
    </aside>
  );
}

function RailExpansionToggle({
  buttonRef,
  controls,
  expanded,
  onToggle,
}: {
  buttonRef: Ref<HTMLButtonElement>;
  controls: string;
  expanded: boolean;
  onToggle: () => void;
}): ReactElement {
  const t = useUiT();
  const label = expanded
    ? t("chrome.collapseAppRail")
    : t("chrome.expandAppRail");
  return (
    <Tooltip label={label} side="right">
      <Button
        ref={buttonRef}
        type="button"
        variant="icon"
        size="iconSm"
        aria-label={label}
        aria-expanded={expanded}
        aria-controls={controls}
        onClick={onToggle}
        className="text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi"
      >
        <Glyph name="app-rail" />
      </Button>
    </Tooltip>
  );
}

function RailSettingsItem({
  active,
  expanded,
  icon,
  label,
  to,
  pathname,
  onActiveToggle,
}: {
  active: boolean;
  expanded: boolean;
  icon: string;
  label: string;
  to: string;
  pathname: string;
  onActiveToggle?: (() => void) | undefined;
}): ReactElement {
  const link = (
    <Link
      to={to}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      data-active={active}
      {...activeLinkToggleProps(to, pathname, onActiveToggle, expanded)}
      className={cn(
        expanded
          ? appRailTreeVariants().link()
          : cn(RAIL_BUTTON, active && RAIL_BUTTON_ACTIVE),
      )}
    >
      <Glyph name={icon} fallbackName="help" size={16} aria-hidden="true" />
      {expanded ? <span className="min-w-0 flex-1 truncate">{label}</span> : null}
    </Link>
  );
  return (
    <div className={cn("flex w-full", expanded ? "px-2" : "justify-center")}>
      {expanded ? link : <Tooltip label={label} side="right">{link}</Tooltip>}
    </div>
  );
}

function SortableRail({
  activeRootId,
  defaultItemId,
  expanded,
  items,
  pathname,
  onActiveToggle,
  onItemLongPress,
  onOrderChange,
}: {
  activeRootId: string | undefined;
  defaultItemId: string | null;
  expanded: boolean;
  items: readonly ChromeMenuNode[];
  pathname: string;
  onActiveToggle?: (() => void) | undefined;
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
  const sensors = useDndKitSensors(6);
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
      if (!overId || draggedId === overId) {
        // A lifted-then-returned drag still emits a trailing click — swallow
        // it so it neither navigates nor toggles.
        suppressNextClick();
        return;
      }
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
    suppressNextClick();
  }, [clearLongPressTimer, suppressNextClick]);

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCenter}
      onDragStart={handleDragStart}
      onDragEnd={handleDragEnd}
      onDragCancel={handleDragCancel}
    >
      <SortableContext items={railOrder} strategy={verticalListSortingStrategy}>
        <div className="flex flex-col items-center gap-1">
          {railItems.map((item) => {
            const toggleProps = activeLinkToggleProps(
              item.target,
              pathname,
              onActiveToggle,
              expanded,
            );
            return (
            <RailItem
              key={item.id}
              item={item}
              active={activeRootId === item.id}
              ariaExpanded={toggleProps["aria-expanded"]}
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
                if (suppressClickRef.current) {
                  event.preventDefault();
                  return;
                }
                toggleProps.onClick?.(event);
              }}
            />
            );
          })}
        </div>
      </SortableContext>
    </DndContext>
  );
}

function RailItem({
  active,
  ariaExpanded,
  defaultApp,
  dragging,
  item,
  onClick,
  onKeyboardMove,
  onLongPressCancel,
  onLongPressEnd,
  onLongPressStart,
}: {
  active: boolean;
  ariaExpanded?: boolean | undefined;
  defaultApp: boolean;
  dragging: boolean;
  item: ChromeMenuNode;
  onClick: (event: MouseEvent<HTMLElement>) => void;
  onKeyboardMove: (event: KeyboardEvent<HTMLElement>) => void;
  onLongPressCancel: (
    item: ChromeMenuNode,
    event: PointerEvent<HTMLElement>,
  ) => void;
  onLongPressEnd: (
    item: ChromeMenuNode,
    event: PointerEvent<HTMLElement>,
  ) => void;
  onLongPressStart: (
    item: ChromeMenuNode,
    event: PointerEvent<HTMLElement>,
  ) => void;
}): ReactElement | null {
  const t = useUiT();
  const sortable = useSortable({
    id: item.id,
    data: { type: "app-rail-item", itemId: item.id },
  });
  const target = item.target;
  if (!target) return null;
  // Strip dnd-kit's screen-reader affordances: the role/instructions describe
  // a keyboard drag path this rail replaces with Alt+Arrow, and the transient
  // pressed state would misread on a link.
  const {
    role: _dragRole,
    "aria-describedby": _dragDescription,
    "aria-roledescription": _dragRoleDescription,
    "aria-pressed": _dragPressed,
    ...dragAttributes
  } = sortable.attributes;
  const label = item.displayLabel;
  const title = defaultApp
    ? t("chrome.defaultRailItemHint", { label })
    : t("chrome.railItemHint", { label });
  return (
    <div
      ref={sortable.setNodeRef}
      style={sortableRailTransformStyle(sortable.transform, sortable.transition)}
      className={cn(
        "w-9 shrink-0 will-change-transform",
        (dragging || sortable.isDragging)
          && "z-10 scale-[1.02] opacity-95 shadow-lg ring-1 ring-brand/50",
      )}
    >
      <Tooltip label={title} side="right">
        <Link
          to={target}
          aria-label={label}
          aria-current={active ? "page" : undefined}
          aria-expanded={ariaExpanded}
          draggable={false}
          onPointerDown={(event) => {
            sortable.listeners?.onPointerDown?.(event);
            onLongPressStart(item, event);
          }}
          onPointerUp={(event) => onLongPressEnd(item, event)}
          onPointerCancel={(event) => onLongPressCancel(item, event)}
          onKeyDown={onKeyboardMove}
          onClick={onClick}
          className={cn(
            RAIL_BUTTON,
            active && RAIL_BUTTON_ACTIVE,
            "cursor-grab select-none touch-none active:cursor-grabbing",
          )}
          {...dragAttributes}
        >
          <span className={item.tone ? toneGlyph(item.tone) : undefined}>
            <Glyph name={item.iconName} fallbackName="help" size={16} />
          </span>
          {defaultApp ? (
            <span
              aria-hidden="true"
              className="absolute bottom-1 right-1 size-1.5 rounded-full border border-rail bg-success"
            />
          ) : null}
        </Link>
      </Tooltip>
    </div>
  );
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

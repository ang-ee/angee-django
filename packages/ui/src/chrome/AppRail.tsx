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
import { Link, useLinkProps } from "@tanstack/react-router";
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

import { useHrefLinkOptions } from "./href-link-options";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { useDndKitSensors } from "../lib/dnd";
import { toneGlyph } from "../lib/tones";
import { LARGE_VIEWPORT_QUERY, useMediaQuery } from "../lib/use-media-query";
import { Button } from "../ui/button";
import { ScrollArea } from "../ui/scroll-area";
import { Tooltip } from "../ui/tooltip";
import { AppBrand } from "./AppBrand";
import { AppChooser } from "./AppChooser";
import { AppRailTree, appRailTreeVariants } from "./AppRailTree";
import { useDeveloperRail, type DeveloperRail } from "./DeveloperMode";
import { Glyph } from "./Glyph";
import type { ChromeMenuItem, ChromeMenuNode } from "./menu-tree";
import { ChromePlaceProvider, useChromePlace } from "./refine-menu";
import {
  railLinkToggleProps,
  moveRailItem,
  orderedRailItems,
  railSortableMove,
  resolvedRailExpanded,
  sameRailOrder,
  type RailDropPlacement,
} from "./app-rail-model";
import { useAppRailPreferences } from "./app-rail-preferences";
import { readRuntimeRouteShortcuts, useAppRuntime, useRuntimeBrand, useRuntimeUserPreferences } from "../runtime";

export interface AppRailProps {
  className?: string;
  /** Menu declarations to project instead of the composed runtime menu. */
  menuItems?: readonly ChromeMenuItem[];
  /** Publishes the rail's current width so the shell grid can track it. */
  onWidthChange?: (width: string | null) => void;
  /** Render as the full-width contents of the compact navigation drawer. */
  presentation?: "rail" | "drawer";
  /** Request temporary navigation when the viewport cannot expand the rail. */
  onOpenNavigation?: ((target: string) => void) | undefined;
  /** The app or Settings destination to expand in the navigation drawer. */
  navigationTarget?: string | null;
}

export const APP_RAIL_COLLAPSED_WIDTH = "var(--spacing-rail-w)";
export const APP_RAIL_EXPANDED_WIDTH = "var(--spacing-rail-expanded-w)";

const RAIL_BUTTON =
  "group relative grid size-9 place-content-center rounded-6 text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring";
const RAIL_BUTTON_ACTIVE =
  "bg-rail-hi text-on-rail-hi before:absolute before:-left-[7px] before:top-1/2 before:h-[18px] before:w-[3px] before:-translate-y-1/2 before:rounded-r-2 before:bg-brand before:content-['']";

/** Icon names supplement accessible labels; developer descriptions follow them in either mode. */
function railTooltip(expanded: boolean, label: string, description?: string): string | undefined {
  return [!expanded ? label : undefined, description].filter(Boolean).join(" · ") || undefined;
}

/**
 * The global app rail: compact icons or one in-place accordion navigation
 * tree. Clicking the active app (or Settings) a second time toggles the
 * desktop expansion. At intermediate widths, roots with included apps request
 * the shell's temporary navigation drawer. Desktop expansion toggles sit at
 * the expanded header's edge and the rail's foot, outside the scrolling list.
 */
export function AppRail({ menuItems, ...props }: AppRailProps): ReactElement {
  return <ChromePlaceProvider menuItems={menuItems}><AppRailBody {...props} /></ChromePlaceProvider>;
}

function AppRailBody({
  className,
  onWidthChange,
  presentation = "rail",
  onOpenNavigation,
  navigationTarget,
}: Omit<AppRailProps, "menuItems">): ReactElement {
  const t = useUiT();
  const rail = useDeveloperRail();
  const { tree, pathname, match, railPlace: activePlace } = useChromePlace();
  const brand = useRuntimeBrand();
  const { confineTo } = useAppRuntime();
  const { railPreferences, setRailPreferences } = useAppRailPreferences();
  const runtimePreferences = useRuntimeUserPreferences();
  const shortcuts = useMemo(
    () => readRuntimeRouteShortcuts(runtimePreferences.preferences).filter(
      (shortcut) => !confineTo || tree.activeAppRoot(shortcut.path)?.id === confineTo,
    ),
    [confineTo, runtimePreferences.preferences, tree],
  );
  const largeViewport = useMediaQuery(LARGE_VIEWPORT_QUERY);
  const drawerMode = presentation === "drawer";
  const expanded = drawerMode || resolvedRailExpanded(
    railPreferences.expanded,
    largeViewport,
  );
  const selectedAppId = match?.trail[0]?.id;
  const selectedSubAppId = match?.app?.parentNode ? match.app.id : undefined;
  const pageId = match?.item.id;
  const place = useMemo(() => drawerMode && navigationTarget
    ? tree.railPlace(navigationTarget, rail.enabled)
    : activePlace, [tree, drawerMode, navigationTarget, activePlace, rail.enabled]);
  const settingsActive = place.scope === "settings";
  const items = useMemo(
    () => orderedRailItems(tree.railMenuItems(), railPreferences.order),
    [railPreferences.order, tree],
  );
  // Developer mode lists hidden apps in the expanded tree; the icon rail keeps its order and default.
  const treeItems = useMemo(
    () => rail.enabled ? orderedRailItems(tree.railMenuItems(true), railPreferences.order) : items,
    [items, rail.enabled, railPreferences.order, tree],
  );
  const [onlyRoot] = items;
  const railBrand = brand ?? (confineTo && onlyRoot
    ? { name: onlyRoot.displayLabel, mark: onlyRoot.iconName } : null);
  const singleApp = railBrand && items.length === 1 && onlyRoot
    ? { root: onlyRoot, brand: railBrand }
    : null;
  const settings = tree.settingsEntry();
  const activeRootId = activePlace.scope === place.scope
    ? activePlace.activeRootId
    : null;
  const openNavigation = !largeViewport && !drawerMode
    ? onOpenNavigation
    : undefined;
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
  // Collapsing can unmount the activated link or header toggle; focus the
  // footer control shared by both modes.
  const toggleAndFocusFooter = useCallback(() => {
    focusFooterToggle.current = true;
    toggleExpanded();
  }, [toggleExpanded]);
  const onActiveToggle = largeViewport && !drawerMode
    ? toggleAndFocusFooter
    : undefined;
  const width = drawerMode
    ? "100%"
    : expanded ? APP_RAIL_EXPANDED_WIDTH : APP_RAIL_COLLAPSED_WIDTH;
  const navId = useId();

  // The width and the grid's --rail-current-w are one layout fact — publish
  // before paint so both land in the same frame.
  useLayoutEffect(() => {
    if (!drawerMode) onWidthChange?.(width);
  }, [drawerMode, onWidthChange, width]);
  useLayoutEffect(
    () => () => {
      if (!drawerMode) onWidthChange?.(null);
    },
    [drawerMode, onWidthChange],
  );
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
        {singleApp ? (
          <Tooltip label={railTooltip(expanded, singleApp.brand.name, rail.describe(singleApp.root))} side="right">
            <AppBrand name={singleApp.brand.name} mark={<Glyph name={singleApp.brand.mark} size={16} />}
              to={singleApp.root.target} compact={!expanded} />
          </Tooltip>
        ) : <AppChooser menuItems={tree} className="shrink-0 text-on-rail-hi" />}
        {expanded && !singleApp ? (
          <span className="min-w-0 flex-1 truncate text-13 font-semibold text-on-rail-hi">
            {t("chrome.apps")}
          </span>
        ) : null}
        {expanded && largeViewport && !drawerMode ? (
          <RailExpansionToggle controls={navId} expanded={expanded} onToggle={toggleAndFocusFooter} className="ml-auto" />
        ) : null}
      </div>
      <nav
        id={navId}
        aria-label={t("chrome.primaryNav")}
        data-rail-list="true"
        className="min-h-0 w-full flex-1"
      >
        <ScrollArea
          className="h-full"
          viewportClassName="overflow-x-hidden"
          contentClassName="min-w-0 pb-2"
        >
          {expanded ? (
            <AppRailTree
              scope={place.scope}
              flat={Boolean(singleApp) && !settingsActive}
              roots={settingsActive ? place.roots : treeItems}
              activeRootId={activeRootId}
              selectedAppId={selectedAppId}
              selectedSubAppId={selectedSubAppId}
              pageId={pageId}
              defaultOpenRootId={place.activeRootId}
              onActiveToggle={onActiveToggle}
            />
          ) : singleApp ? (
            <div className="flex flex-col gap-1">
              {singleApp.root.appChildren().map((item) => item.target ? (
                <RailSettingsItem key={item.id} active={selectedSubAppId === item.id} currentPage={pageId === item.id} expanded={false}
                  icon={item.iconName} label={rail.label(item)} to={item.target} pathname={pathname}
                  description={rail.describe(item)}
                  onActiveToggle={onActiveToggle} />
              ) : null)}
            </div>
          ) : (
            <SortableRail
              rail={rail}
              items={items}
              activeRootId={settingsActive ? undefined : activeRootId ?? undefined}
              pageId={pageId}
              defaultItemId={defaultItemId}
              expanded={expanded}
              pathname={pathname}
              onActiveToggle={onActiveToggle}
              onItemLongPress={handleItemLongPress}
              onOpenNavigation={openNavigation}
              onOrderChange={handleOrderChange}
            />
          )}
          {!settingsActive && shortcuts.length ? (
            <>
              <div className={cn("my-2 h-px bg-border-on-rail", expanded ? "mx-2" : "mx-auto w-6")} aria-hidden="true" />
              <div className="flex flex-col gap-1">
                {shortcuts.map((shortcut) => (
                  <RuntimeShortcutItem
                    key={shortcut.id}
                    expanded={expanded}
                    icon={shortcut.icon ?? "dashboard"}
                    label={shortcut.label}
                    pathname={pathname}
                    to={shortcut.path}
                  />
                ))}
              </div>
            </>
          ) : null}
        </ScrollArea>
      </nav>
      {settings ? (
        <div className="shrink-0 border-t border-border-on-rail pt-2">
          <RailSettingsItem
            active={activePlace.scope === "settings"}
            expanded={expanded}
            icon={settings.icon}
            label={t("chrome.settings")}
            to={settings.target}
            onOpenNavigation={openNavigation}
            pathname={pathname}
            onActiveToggle={onActiveToggle}
          />
        </div>
      ) : null}
      {largeViewport && !drawerMode ? (
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
            onToggle={toggleAndFocusFooter}
          />
        </div>
      ) : null}
    </aside>
  );
}

function RuntimeShortcutItem({ expanded, icon, label, pathname, to }: {
  expanded: boolean;
  icon: string;
  label: string;
  pathname: string;
  to: string;
}): ReactElement {
  const active = pathname === to;
  const hrefOptions = useHrefLinkOptions(to);
  const link = (
    <Link
      {...hrefOptions}
      aria-label={label}
      aria-current={active ? "page" : undefined}
      data-current={active}
      className={cn(expanded ? appRailTreeVariants().link() : cn(RAIL_BUTTON, active && RAIL_BUTTON_ACTIVE))}
    >
      <Glyph name={icon} fallbackName="dashboard" size={16} aria-hidden="true" />
      {expanded ? <span className="min-w-0 flex-1 truncate">{label}</span> : null}
    </Link>
  );
  return <div className={cn("flex w-full", expanded ? "px-2" : "justify-center")}>
    <Tooltip label={railTooltip(expanded, label)} side="right">{link}</Tooltip>
  </div>;
}

function RailExpansionToggle({
  buttonRef,
  controls,
  expanded,
  onToggle,
  className,
}: {
  buttonRef?: Ref<HTMLButtonElement>;
  controls: string;
  expanded: boolean;
  onToggle: () => void;
  className?: string;
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
        className={cn("text-on-rail-mut hover:bg-rail-hi hover:text-on-rail-hi", className)}
      >
        <Glyph name="app-rail" />
      </Button>
    </Tooltip>
  );
}

function RailSettingsItem({
  active,
  currentPage = false,
  expanded,
  icon,
  label,
  description,
  to,
  pathname,
  onActiveToggle,
  onOpenNavigation,
}: {
  active: boolean;
  currentPage?: boolean;
  expanded: boolean;
  icon: string;
  label: string;
  description?: string | undefined;
  to: string;
  pathname: string;
  onActiveToggle?: (() => void) | undefined;
  onOpenNavigation?: ((target: string) => void) | undefined;
}): ReactElement {
  const hrefOptions = useHrefLinkOptions(to);
  const linkProps = useLinkProps({ ...hrefOptions,
    ...railLinkToggleProps(to, pathname, onActiveToggle, expanded, onOpenNavigation),
  });
  const link = (
    <a
      {...linkProps}
      aria-label={label}
      aria-current={active ? (currentPage ? "page" : "true") : undefined}
      data-current={active}
      data-status={undefined}
      className={cn(
        expanded
          ? appRailTreeVariants().link()
          : cn(RAIL_BUTTON, active && RAIL_BUTTON_ACTIVE),
      )}
    >
      <Glyph name={icon} fallbackName="help" size={16} aria-hidden="true" />
      {expanded ? <span className="min-w-0 flex-1 truncate">{label}</span> : null}
    </a>
  );
  return (
    <div className={cn("flex w-full", expanded ? "px-2" : "justify-center")}>
      <Tooltip label={railTooltip(expanded, label, description)} side="right">{link}</Tooltip>
    </div>
  );
}

function SortableRail({
  rail,
  activeRootId,
  pageId,
  defaultItemId,
  expanded,
  items,
  pathname,
  onActiveToggle,
  onOpenNavigation,
  onItemLongPress,
  onOrderChange,
}: {
  rail: DeveloperRail;
  activeRootId: string | undefined;
  pageId?: string;
  defaultItemId: string | null;
  expanded: boolean;
  items: readonly ChromeMenuNode[];
  pathname: string;
  onActiveToggle?: (() => void) | undefined;
  onOpenNavigation?: ((target: string) => void) | undefined;
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
            const toggleProps = railLinkToggleProps(
              item.target,
              pathname,
              onActiveToggle,
              expanded,
              item.appChildren().length ? onOpenNavigation : undefined,
            );
            return (
            <RailItem
              rail={rail}
              key={item.id}
              item={item}
              active={activeRootId === item.id}
              currentPage={pageId === item.id}
              ariaExpanded={toggleProps["aria-expanded"]}
              ariaHasPopup={toggleProps["aria-haspopup"]}
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
  rail,
  active,
  currentPage,
  ariaExpanded,
  ariaHasPopup,
  defaultApp,
  dragging,
  item,
  onClick,
  onKeyboardMove,
  onLongPressCancel,
  onLongPressEnd,
  onLongPressStart,
}: {
  rail: DeveloperRail;
  active: boolean;
  currentPage: boolean;
  ariaExpanded?: boolean | undefined;
  ariaHasPopup?: "dialog" | undefined;
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
  const hrefOptions = useHrefLinkOptions(target);
  const linkProps = useLinkProps({
    ...hrefOptions, onClick, onKeyDown: onKeyboardMove,
    onPointerDown: (event) => {
      sortable.listeners?.onPointerDown?.(event);
      onLongPressStart(item, event);
    },
    onPointerUp: (event) => onLongPressEnd(item, event),
    onPointerCancel: (event) => onLongPressCancel(item, event),
  });
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
  const label = rail.label(item);
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
      <Tooltip label={railTooltip(false, title, rail.describe(item))} side="right">
        <a
          {...linkProps}
          aria-label={label}
          aria-current={active ? (currentPage ? "page" : "true") : undefined}
          data-current={active}
          data-status={undefined}
          aria-expanded={ariaExpanded}
          aria-haspopup={ariaHasPopup}
          draggable={false}
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
        </a>
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

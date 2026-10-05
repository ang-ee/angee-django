import {
  useCallback,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  type ReactElement,
  type Ref,
} from "react";
import { Link, useLinkProps } from "@tanstack/react-router";

import { useHrefLinkOptions } from "./href-link-options";
import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
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
  orderedRailItems,
  railTooltip,
  resolvedRailExpanded,
} from "./app-rail-model";
import { useAppRailPreferences } from "./app-rail-preferences";
import { RailDefaultMark, RailSortable, useRailSortableItem, type RailSorting } from "./RailSortable";
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

/**
 * The global app rail: compact icons or one in-place accordion navigation
 * tree. Clicking the active app (or Settings) a second time toggles the
 * desktop expansion, and opening the app chooser expands it. At intermediate
 * widths, roots with included apps request the shell's temporary navigation
 * drawer. Desktop expansion toggles sit at the expanded header's edge and the
 * rail's foot, outside the scrolling list. Both rail modes reorder the app
 * roots and set the default app through one `RailSortable`.
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
  // Developer mode lists hidden apps in the expanded tree, placed and reordered by the
  // same order; only the icon rail's apps can be the default.
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
  // Only the desktop rail expands; temporary navigation never changes the preference.
  const expandable = largeViewport && !drawerMode;
  const itemIds = useMemo(() => items.map((item) => item.id), [items]);
  const defaultItemId = itemIds.includes(railPreferences.defaultItemId ?? "")
    ? railPreferences.defaultItemId
    : null;
  const sorting = useMemo<RailSorting>(() => ({
    defaultItemId,
    onOrderChange: (order) => setRailPreferences({ ...railPreferences, order }),
    // Home resolves the default among the icon rail's apps only.
    onItemLongPress: (item) => {
      if (item.id === defaultItemId || !itemIds.includes(item.id)) return;
      setRailPreferences({ ...railPreferences, defaultItemId: item.id });
    },
  }), [defaultItemId, itemIds, railPreferences, setRailPreferences]);
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
  const onActiveToggle = expandable ? toggleAndFocusFooter : undefined;
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
        ) : <AppChooser menuItems={tree} className="shrink-0 text-on-rail-hi"
          onOpen={expandable && !expanded ? toggleExpanded : undefined} />}
        {expanded && !singleApp ? (
          <span className="min-w-0 flex-1 truncate text-13 font-semibold text-on-rail-hi">
            {t("chrome.apps")}
          </span>
        ) : null}
        {expanded && expandable ? (
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
              sorting={drawerMode ? undefined : sorting}
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
            <RailSortable items={items} {...sorting}>
              <div className="flex flex-col items-center gap-1">
                {items.map((item) => (
                  <RailItem
                    key={item.id}
                    rail={rail}
                    item={item}
                    active={activeRootId === item.id}
                    currentPage={pageId === item.id}
                    pathname={pathname}
                    onActiveToggle={onActiveToggle}
                    onOpenNavigation={item.appChildren().length ? openNavigation : undefined}
                  />
                ))}
              </div>
            </RailSortable>
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
      {expandable ? (
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

/** One icon of the collapsed rail's `RailSortable`. */
function RailItem({
  rail,
  active,
  currentPage,
  item,
  pathname,
  onActiveToggle,
  onOpenNavigation,
}: {
  rail: DeveloperRail;
  active: boolean;
  currentPage: boolean;
  item: ChromeMenuNode;
  pathname: string;
  onActiveToggle?: (() => void) | undefined;
  onOpenNavigation?: ((target: string) => void) | undefined;
}): ReactElement {
  const label = rail.label(item);
  const sortable = useRailSortableItem(item, label, rail.describe(item));
  const hrefOptions = useHrefLinkOptions(item.target);
  const linkProps = useLinkProps({ ...hrefOptions,
    ...railLinkToggleProps(item.target, pathname, onActiveToggle, false, onOpenNavigation),
  });
  return (
    <div {...sortable.node} className={cn("w-9 shrink-0", sortable.node.className)}>
      <Tooltip label={sortable.tooltip} side="right">
        <a
          {...linkProps}
          {...sortable.link}
          aria-label={label}
          aria-current={active ? (currentPage ? "page" : "true") : undefined}
          data-current={active}
          data-status={undefined}
          className={cn(RAIL_BUTTON, active && RAIL_BUTTON_ACTIVE, sortable.link.className)}
        >
          <span className={item.tone ? toneGlyph(item.tone) : undefined}>
            <Glyph name={item.iconName} fallbackName="help" size={16} />
          </span>
          {sortable.defaultApp ? <RailDefaultMark className="bottom-1 right-1" /> : null}
        </a>
      </Tooltip>
    </div>
  );
}

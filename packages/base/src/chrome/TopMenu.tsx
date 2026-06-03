import type { ReactElement } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { useMenus } from "@angee/sdk";
import { parseAsStringLiteral, useQueryState } from "nuqs";

import { cn } from "../lib/cn";
import { useDataViewMaybe, type DataViewFilter } from "../views";
import { Glyph } from "./Glyph";
import {
  NavigationMenu,
  navigationMenuVariants,
} from "../ui/navigation-menu";
import {
  appSectionItems,
  buildMenuTree,
  menuItemIcon,
  menuItemLabel,
  menuItemMatchesPath,
  menuItemTarget,
  topMenuItems,
  type ChromeMenuItem,
} from "./menu-tree";

const TOP_TAB_IDS = ["all", "starred", "archive"] as const;
export type TopMenuTabId = (typeof TOP_TAB_IDS)[number];

export interface TopMenuTab {
  id: TopMenuTabId;
  label: string;
  icon?: string;
  filter: DataViewFilter;
}

const DEFAULT_TABS: readonly TopMenuTab[] = [
  { id: "all", label: "All notes", icon: "list", filter: {} },
  { id: "starred", label: "Starred", icon: "star", filter: { isStarred: true } },
  {
    id: "archive",
    label: "Archive",
    icon: "archive",
    filter: { status: { exact: "ARCHIVED" } },
  },
];

const tabClass =
  "inline-flex h-8 min-w-0 items-center gap-2 rounded-md px-3 text-13 font-medium text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring aria-selected:bg-rail-hi aria-selected:text-on-rail-hi";

export interface TopMenuProps {
  className?: string;
  items?: readonly ChromeMenuItem[];
  tabs?: readonly TopMenuTab[];
}

export type TopMenuItem = ChromeMenuItem;

export function TopMenu({
  className,
  items,
  tabs,
}: TopMenuProps): ReactElement | null {
  if (tabs !== undefined) {
    return <TopMenuTabs className={className} tabs={tabs} />;
  }

  return <TopMenuLinks className={className} items={items} />;
}

function TopMenuTabs({
  className,
  tabs = DEFAULT_TABS,
}: {
  className?: string;
  tabs?: readonly TopMenuTab[];
}): ReactElement | null {
  const dataView = useDataViewMaybe();
  const [activeTab, setActiveTab] = useQueryState(
    "tab",
    parseAsStringLiteral(TOP_TAB_IDS).withDefault("all"),
  );

  if (!tabs.length) return null;

  return (
    <div
      role="tablist"
      aria-label="Collection views"
      className={cn("flex min-w-0 gap-1", className)}
    >
      {tabs.map((tab) => (
        <TopMenuTabButton
          key={tab.id}
          tab={tab}
          active={activeTab === tab.id}
          onSelect={() => {
            void setActiveTab(tab.id);
            dataView?.setFilter(tab.filter);
          }}
        />
      ))}
    </div>
  );
}

function TopMenuLinks({
  className,
  items,
}: {
  className?: string;
  items?: readonly ChromeMenuItem[];
}): ReactElement | null {
  const runtimeItems = useMenus() as readonly ChromeMenuItem[];
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const tree = buildMenuTree(items ?? runtimeItems);
  // An explicit `items` list is a shell scoping its own nav — render it as given.
  // The default top bar is the *active app's* sections: the rail switches apps,
  // the top bar navigates within the one you're in, so a sibling app never leaks.
  const menuItems = items ? topMenuItems(tree) : appSectionItems(tree, pathname);
  const hasPopup = menuItems.some((item) => Boolean(item.children?.length));

  if (!menuItems.length) return null;

  return (
    <NavigationMenu.Root className={cn("min-w-0", className)}>
      <NavigationMenu.List>
        {menuItems.map((item) => (
          <TopMenuLinkItem key={item.id} item={item} pathname={pathname} />
        ))}
      </NavigationMenu.List>
      {hasPopup ? (
        <NavigationMenu.Portal>
          <NavigationMenu.Positioner sideOffset={8}>
            <NavigationMenu.Popup>
              <NavigationMenu.Viewport />
              <NavigationMenu.Arrow />
            </NavigationMenu.Popup>
          </NavigationMenu.Positioner>
        </NavigationMenu.Portal>
      ) : null}
    </NavigationMenu.Root>
  );
}

function TopMenuLinkItem({
  item,
  pathname,
}: {
  item: ChromeMenuItem;
  pathname: string;
}): ReactElement {
  const children = item.children?.filter((child) => menuItemTarget(child)) ?? [];
  const active = menuItemIsActive(item, pathname);
  const target = menuItemTarget(item);
  const label = menuItemLabel(item);
  const icon = menuItemIcon(item);

  if (children.length) {
    return (
      <NavigationMenu.Item value={item.id}>
        <NavigationMenu.Trigger active={active} hasPopup>
          <Glyph name={icon} />
          <span className="truncate">{label}</span>
          <NavigationMenu.Icon />
        </NavigationMenu.Trigger>
        <NavigationMenu.Content>
          <div className="grid w-80 gap-1">
            {children.map((child) => (
              <TopMenuPanelLink
                key={child.id}
                item={child}
                active={menuItemIsActive(child, pathname)}
              />
            ))}
          </div>
        </NavigationMenu.Content>
      </NavigationMenu.Item>
    );
  }

  if (!target) {
    return (
      <NavigationMenu.Item value={item.id}>
        <NavigationMenu.Text active={active}>
          <Glyph name={icon} />
          <span className="truncate">{label}</span>
        </NavigationMenu.Text>
      </NavigationMenu.Item>
    );
  }

  const styles = navigationMenuVariants({ active, hasPopup: false });
  return (
    <NavigationMenu.Item value={item.id}>
      <Link
        to={target}
        aria-current={active ? "page" : undefined}
        className={styles.link()}
      >
        <Glyph name={icon} />
        <span className="truncate">{label}</span>
      </Link>
    </NavigationMenu.Item>
  );
}

function TopMenuPanelLink({
  active,
  item,
}: {
  active: boolean;
  item: ChromeMenuItem;
}): ReactElement | null {
  const target = menuItemTarget(item);
  if (!target) return null;
  return (
    <Link
      to={target}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex h-auto items-start gap-3 rounded-md px-3 py-2 text-fg no-underline outline-none transition-colors hover:bg-inset hover:text-fg focus-visible:focus-ring",
        active && "bg-brand-soft text-brand-soft-text hover:bg-brand-soft",
      )}
    >
      <span className="mt-0.5 grid size-7 shrink-0 place-content-center rounded bg-brand-soft text-brand-soft-text">
        <Glyph name={menuItemIcon(item)} />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-13 font-semibold">
          {menuItemLabel(item)}
        </span>
        {item.description ? (
          <span className="block truncate text-xs text-fg-muted">
            {item.description}
          </span>
        ) : null}
      </span>
    </Link>
  );
}

function TopMenuTabButton({
  tab,
  active,
  onSelect,
}: {
  tab: TopMenuTab;
  active: boolean;
  onSelect: () => void;
}): ReactElement {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      className={tabClass}
      onClick={onSelect}
    >
      {tab.icon ? <Glyph name={tab.icon} size={14} className="shrink-0" /> : null}
      <span className="truncate">{tab.label}</span>
    </button>
  );
}

function menuItemIsActive(item: ChromeMenuItem, pathname: string): boolean {
  return (
    menuItemMatchesPath(item, pathname) ||
    Boolean(item.children?.some((child) => menuItemMatchesPath(child, pathname)))
  );
}

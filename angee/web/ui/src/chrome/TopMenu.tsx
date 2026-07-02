import type { ReactElement } from "react";
import { Link, useRouterState } from "@tanstack/react-router";
import { parseAsString, useQueryState } from "nuqs";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { Glyph } from "./Glyph";
import {
  NavigationMenu,
  navigationMenuVariants,
} from "../ui/navigation-menu";
import { textRoleVariants } from "../ui/text";
import {
  type ChromeMenuItem,
  type ChromeMenuNode,
  MenuTree,
} from "./menu-tree";
import { useChromeMenuTree } from "./refine-menu";

/**
 * A presentational collection-view tab. The framework owns the tab strip and
 * its `?tab=` query state; the product owns what each tab *means* — a route or
 * data view reads `?tab=` and applies its own filter. No resource-view coupling
 * lives here.
 */
export interface TopMenuTab {
  id: string;
  label: string;
  icon?: string;
}

const tabClass =
  "inline-flex h-8 min-w-0 items-center gap-2 rounded-6 px-3 text-13 font-medium text-on-rail-mut outline-none transition-colors hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring aria-selected:bg-rail-hi aria-selected:text-on-rail-hi";

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
  tabs,
}: {
  className?: string;
  tabs: readonly TopMenuTab[];
}): ReactElement | null {
  const t = useUiT();
  const [rawTab, setActiveTab] = useQueryState("tab", parseAsString);

  const [firstTab] = tabs;
  if (!firstTab) return null;

  // The first tab is the default; an unknown `?tab=` falls back to it.
  const activeId = tabs.some((tab) => tab.id === rawTab) ? rawTab : firstTab.id;

  return (
    <div
      role="tablist"
      aria-label={t("chrome.collectionViews")}
      className={cn("flex min-w-0 gap-1", className)}
    >
      {tabs.map((tab) => (
        <TopMenuTabButton
          key={tab.id}
          tab={tab}
          active={activeId === tab.id}
          onSelect={() => {
            void setActiveTab(tab.id);
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
  const runtimeTree = useChromeMenuTree();
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  });
  const tree = items ? MenuTree.from(items) : runtimeTree;
  // An explicit `items` list is a layout scoping its own nav — render it as given.
  // The default top bar is the *active app's* sections: the rail switches apps,
  // the top bar navigates within the one you're in, so a sibling app never leaks.
  const menuItems = items ? tree.railMenuItems() : tree.appSectionItems(pathname);
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
  item: ChromeMenuNode;
  pathname: string;
}): ReactElement {
  const children = item.targetedChildren;
  const active = item.isActive(pathname);
  const target = item.target;
  const label = item.displayLabel;
  const icon = item.iconName;

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
                active={child.isActive(pathname)}
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
  item: ChromeMenuNode;
}): ReactElement | null {
  const target = item.target;
  if (!target) return null;
  return (
    <Link
      to={target}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex h-auto items-start gap-3 rounded-6 px-3 py-2 text-fg no-underline outline-none transition-colors hover:bg-inset hover:text-fg focus-visible:focus-ring",
        active && "bg-brand-soft text-brand-soft-text hover:bg-brand-soft",
      )}
    >
      <span className="mt-0.5 grid size-7 shrink-0 place-content-center rounded-6 bg-brand-soft text-brand-soft-text">
        <Glyph name={item.iconName} />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-13 font-semibold">
          {item.displayLabel}
        </span>
        {item.description ? (
          <span className={cn(textRoleVariants({ role: "caption", truncate: true }), "block")}>
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

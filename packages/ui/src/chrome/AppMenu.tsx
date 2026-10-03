import type { ComponentPropsWithRef, ReactElement } from "react";
import { createLink, useRouterState } from "@tanstack/react-router";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { DropdownMenu } from "../ui/dropdown-menu";
import { Glyph } from "./Glyph";
import { MenuTree, type ChromeMenuItem, type ChromeMenuNode } from "./menu-tree";
import { useChromeMenuTree } from "./refine-menu";

const menuItemClass = "relative flex h-full shrink-0 items-center gap-1 rounded-4 px-2 text-13 text-on-rail-mut no-underline outline-none hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring data-[current=true]:text-on-rail-hi after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 data-[current=true]:after:bg-brand";

// Router's Link marks every matching ancestor/reference current. The menu's
// single matcher owns that fact; native createLink still owns navigation.
const AppMenuLink = createLink(function MenuAnchor({
  "data-current": current, ...props
}: ComponentPropsWithRef<"a"> & { "data-current"?: boolean }) {
  return <a {...props} data-current={current} aria-current={current ? "page" : undefined} />;
});

export interface AppMenuProps {
  menuItems?: readonly ChromeMenuItem[] | MenuTree;
  className?: string;
}

/** The selected app's own menus; included apps remain in the rail. */
export function AppMenu({ menuItems, className }: AppMenuProps): ReactElement | null {
  const t = useUiT();
  const runtimeTree = useChromeMenuTree();
  const tree = MenuTree.from(menuItems ?? runtimeTree);
  const { pathname, searchStr } = useRouterState({ select: (state) => state.location });
  const match = tree.match(pathname, searchStr);
  const settings = match?.trail[0]?.group === "platform" ? tree.settingsEntry() : undefined;
  const app = match?.app;
  if (!app && !settings) return null;
  const label = settings ? t("chrome.settings") : app!.displayLabel;
  const items = settings?.items ?? app!.menuItems();
  const currentId = match?.item.id;

  return <nav aria-label={t("chrome.appMenu", { label })}
    className={cn("flex h-full min-w-0 items-center gap-1 overflow-x-auto", className)}>
    <AppMenuLink to={settings?.target ?? app?.target} href={settings?.target ?? app?.target}
      data-current={app?.id === currentId}
      className={cn(menuItemClass, "font-semibold")}>
      {label}
    </AppMenuLink>
    {items.map((item) => {
      const children = item.menuItems();
      if (!children.length) {
        return <AppMenuLink key={item.id} to={item.target} href={item.target}
          data-current={item.id === currentId} className={menuItemClass}>
          {item.displayLabel}
        </AppMenuLink>;
      }
      const current = match?.trail.some((node) => node.id === item.id) === true;
      return <DropdownMenu.Root key={item.id} modal={false}>
        <DropdownMenu.Trigger aria-current={current ? "true" : undefined}
          data-current={current} className={menuItemClass}>
          {item.displayLabel}<Glyph name="chevron-down" size={12} aria-hidden="true" />
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Positioner side="bottom" align="start" sideOffset={4}>
            <DropdownMenu.Content>
              <MenuEntries item={item} currentId={currentId} />
            </DropdownMenu.Content>
          </DropdownMenu.Positioner>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>;
    })}
  </nav>;
}

/** Deeper levels are labelled groups in the same native dropdown. */
function MenuEntries({ item, currentId }: { item: ChromeMenuNode; currentId?: string }): ReactElement {
  const children = item.menuItems();
  const ownPage = item.to && !children.some((child) => child.targetPath === item.path);
  return <>
    {ownPage ? <MenuPage item={item} currentId={currentId} /> : null}
    {children.map((child) => child.menuItems().length ? (
      <DropdownMenu.Group key={child.id}>
        {child.displayLabel !== item.displayLabel ? <DropdownMenu.Label>{child.displayLabel}</DropdownMenu.Label> : null}
        <MenuEntries item={child} currentId={currentId} />
      </DropdownMenu.Group>
    ) : <MenuPage key={child.id} item={child} currentId={currentId} />)}
  </>;
}

function MenuPage({ item, currentId }: { item: ChromeMenuNode; currentId?: string }): ReactElement | null {
  if (!item.target) return null;
  return <DropdownMenu.LinkItem href={item.target} closeOnClick
    render={<AppMenuLink to={item.target} href={item.target} data-current={item.id === currentId} />}>
    {item.displayLabel}
  </DropdownMenu.LinkItem>;
}

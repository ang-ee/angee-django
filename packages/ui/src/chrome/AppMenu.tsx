import type { ComponentPropsWithRef, ReactElement } from "react";
import { createLink } from "@tanstack/react-router";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { DropdownMenu } from "../ui/dropdown-menu";
import { Tooltip } from "../ui/tooltip";
import { useDeveloperRail, type DeveloperRail } from "./DeveloperMode";
import { Glyph } from "./Glyph";
import type { MenuTree, ChromeMenuItem, ChromeMenuNode } from "./menu-tree";
import { ChromePlaceProvider, useChromePlace } from "./refine-menu";

const menuItemClass = "relative flex h-full shrink-0 items-center gap-1 rounded-4 px-2 text-13 text-on-rail-mut no-underline outline-none hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring data-[current=true]:text-on-rail-hi after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 data-[current=true]:after:bg-brand";

// Router's Link marks every matching ancestor/reference current. The menu's
// single matcher owns that fact; native createLink still owns navigation.
const AppMenuLink = createLink(function MenuAnchor({
  "data-current": current, ...props
}: ComponentPropsWithRef<"a"> & { "data-current"?: boolean }) {
  return <a {...props} data-status={undefined} data-current={current} aria-current={current ? "page" : undefined} />;
});

export interface AppMenuProps {
  menuItems?: readonly ChromeMenuItem[] | MenuTree;
  className?: string;
}

/** The selected app's own menus; included apps remain in the rail. */
export function AppMenu({ menuItems, className }: AppMenuProps): ReactElement {
  return <ChromePlaceProvider menuItems={menuItems}><AppMenuBody className={className} /></ChromePlaceProvider>;
}

function AppMenuBody({ className }: Pick<AppMenuProps, "className">): ReactElement | null {
  const t = useUiT();
  const rail = useDeveloperRail();
  const { tree, match } = useChromePlace();
  const settings = match?.trail[0]?.group === "platform" ? tree.settingsEntry() : undefined;
  const app = match?.app;
  if (!app && !settings) return null;
  const label = settings ? t("chrome.settings") : app!.displayLabel;
  const items = settings?.items ?? rail.menus(app!);
  const currentId = match?.item.id;

  return <nav aria-label={t("chrome.appMenu", { label })}
    className={cn("flex h-full min-w-0 items-center gap-1 overflow-x-auto", className)}>
    <AppMenuLink to={settings?.target ?? app?.target} href={settings?.target ?? app?.target}
      data-current={app?.id === currentId}
      className={cn(menuItemClass, "font-semibold")}>
      {label}
    </AppMenuLink>
    {items.map((item) => {
      const children = rail.menus(item);
      const removed = rail.removedUnder(item.id);
      if ((!children.length && !removed.length) || (!item.to && children.length === 1 && !removed.length)) {
        const destination = children[0] ?? item;
        return <Tooltip key={item.id} label={rail.describe(destination)} side="bottom">
          <AppMenuLink to={destination.target} href={destination.target}
            data-current={destination.id === currentId} className={menuItemClass}>
            {rail.label(item)}
          </AppMenuLink>
        </Tooltip>;
      }
      const current = match?.trail.some((node) => node.id === item.id) === true;
      return <DropdownMenu.Root key={item.id} modal={false}>
        <DropdownMenu.Trigger aria-current={current ? "true" : undefined}
          data-current={current} className={menuItemClass}>
          {rail.label(item)}<Glyph name="chevron-down" size={12} aria-hidden="true" />
        </DropdownMenu.Trigger>
        <DropdownMenu.Portal>
          <DropdownMenu.Positioner side="bottom" align="start" sideOffset={4}>
            <DropdownMenu.Content>
              <MenuEntries item={item} currentId={currentId} rail={rail} />
            </DropdownMenu.Content>
          </DropdownMenu.Positioner>
        </DropdownMenu.Portal>
      </DropdownMenu.Root>;
    })}
    {app ? <RemovedMenus rail={rail} parentId={app.id} /> : null}
  </nav>;
}

/** Developer mode: menus a layer removed, struck through where they showed, naming who removed them. */
function RemovedMenus({ rail, parentId, inMenu = false }: { rail: DeveloperRail; parentId: string; inMenu?: boolean }): ReactElement | null {
  const t = useUiT();
  const removed = rail.removedUnder(parentId);
  if (!removed.length) return null;
  return <>{removed.map((node) => {
    const label = t("developer.removedBy", { label: node.displayLabel, layer: node.by });
    return inMenu
      ? <DropdownMenu.Item key={node.id} disabled className="line-through">{label}</DropdownMenu.Item>
      : <Tooltip key={node.id} label={node.route ? `${node.id} → ${node.route}` : node.id} side="bottom">
        <span tabIndex={0} className={cn(menuItemClass, "cursor-default line-through")}>{label}</span>
      </Tooltip>;
  })}</>;
}

/** Deeper levels are labelled groups in the same native dropdown. */
function MenuEntries({ item, currentId, rail }: { item: ChromeMenuNode; currentId?: string; rail: DeveloperRail }): ReactElement {
  const children = rail.menus(item);
  const ownPage = item.to && !children.some((child) => child.targetPath === item.path);
  return <>
    {ownPage ? <MenuPage item={item} currentId={currentId} rail={rail} /> : null}
    {children.map((child) => rail.menus(child).length ? (
      <DropdownMenu.Group key={child.id}>
        {child.displayLabel !== item.displayLabel ? <DropdownMenu.Label>{rail.label(child)}</DropdownMenu.Label> : null}
        <MenuEntries item={child} currentId={currentId} rail={rail} />
      </DropdownMenu.Group>
    ) : <MenuPage key={child.id} item={child} currentId={currentId} rail={rail} />)}
    <RemovedMenus rail={rail} parentId={item.id} inMenu />
  </>;
}

function MenuPage({ item, currentId, rail }: { item: ChromeMenuNode; currentId?: string; rail: DeveloperRail }): ReactElement | null {
  if (!item.target) return null;
  const description = rail.describe(item);
  return <DropdownMenu.LinkItem href={item.target} closeOnClick
    render={<AppMenuLink to={item.target} href={item.target} data-current={item.id === currentId} title={description} />}>
    {rail.label(item)}
  </DropdownMenu.LinkItem>;
}

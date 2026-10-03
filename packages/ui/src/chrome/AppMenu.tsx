import { useMemo, type ComponentPropsWithRef, type ReactElement } from "react";
import { createLink } from "@tanstack/react-router";

import { useUiT } from "../i18n";
import { cn } from "../lib/cn";
import { useOverflowCount } from "../lib/use-overflow-count";
import { DropdownMenu } from "../ui/dropdown-menu";
import { Tooltip } from "../ui/tooltip";
import { useDeveloperRail, type DeveloperRail } from "./DeveloperMode";
import { Glyph } from "./Glyph";
import type { MenuTree, ChromeMenuItem, ChromeMenuNode } from "./menu-tree";
import { ChromePlaceProvider, useChromePlace } from "./refine-menu";

const menuItemClass = "relative flex h-full shrink-0 items-center gap-1 rounded-4 px-2 text-13 text-on-rail-mut no-underline outline-none hover:bg-rail-hi hover:text-on-rail-hi focus-visible:focus-ring data-[current=true]:text-on-rail-hi after:absolute after:inset-x-2 after:bottom-0 after:h-0.5 data-[current=true]:after:bg-brand";

// Router's Link marks every matching ancestor/reference current. The menu's
// single matcher owns that fact; native createLink still owns navigation.
function MenuAnchor({
  "data-current": current, ...props
}: ComponentPropsWithRef<"a"> & { "data-current"?: boolean }) {
  return <a {...props} data-status={undefined} data-current={current} aria-current={current ? "page" : undefined} />;
}

const createAppMenuLink = () => createLink(MenuAnchor);
let appMenuLink: ReturnType<typeof createAppMenuLink> | undefined;

// Created on first render rather than at import, so importing the UI barrel
// does not call into the router (suites that mock it partially still load).
function AppMenuLink(props: Parameters<ReturnType<typeof createAppMenuLink>>[0]): ReactElement {
  appMenuLink ??= createAppMenuLink();
  const Link = appMenuLink;
  return <Link {...props} />;
}

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
  const settings = useMemo(() => match?.trail[0]?.group === "platform" ? tree.settingsEntry() : undefined, [tree, match]);
  const app = match?.app;
  const entries = useMemo<readonly AppMenuEntry[]>(() => [
    ...(settings?.items ?? (app ? rail.menus(app) : [])).map((node) => ({ kind: "menu" as const, node })),
    // Removed included apps belong to the rail, as before.
    ...(app ? rail.removedUnder(app.id).filter((node) => !node.app) : []).map((node) => ({ kind: "removed" as const, node })),
  ], [settings, app, rail]);
  const currentIndex = entries.findIndex((entry) => entry.kind === "menu" && match?.trail.some((node) => node.id === entry.node.id));
  const [containerRef, measurementRef, count] = useOverflowCount(entries, currentIndex);
  if (!app && !settings) return null;
  const label = settings ? t("chrome.settings") : app!.displayLabel;
  const currentId = match?.item.id;
  const visible = entries.slice(0, count);
  if (count > 0 && currentIndex >= count) visible[count - 1] = entries[currentIndex]!;
  const visibleIds = new Set(visible.map((entry) => entry.node.id));
  const overflow = entries.filter((entry) => !visibleIds.has(entry.node.id));
  const appLink = <AppMenuLink to={settings?.target ?? app?.target} href={settings?.target ?? app?.target}
    data-current={app?.id === currentId} className={cn(menuItemClass, "font-semibold")}>
    {label}
  </AppMenuLink>;

  return <nav ref={containerRef} aria-label={t("chrome.appMenu", { label })}
    className={cn("relative flex h-full min-w-0 flex-1 items-center gap-1 overflow-hidden", className)}>
    {/* One intrinsic ordered list; inert and hidden so its copies are never navigable. */}
    <div ref={measurementRef} inert aria-hidden="true"
      className="pointer-events-none invisible absolute inset-y-0 left-0 flex w-max items-center gap-1">
      <span className={cn(menuItemClass, "font-semibold")}>{label}</span>
      {entries.map((entry) => <div key={entry.node.id} className="flex h-full shrink-0">
        <AppMenuEntryControl entry={entry} currentId={currentId} current={false} rail={rail} measuring />
      </div>)}
      <span className={menuItemClass}>{t("chrome.more")}<Glyph name="chevron-down" size={12} aria-hidden="true" /></span>
    </div>
    {count > 0 || !overflow.length ? appLink : null}
    {visible.map((entry) => <AppMenuEntryControl key={entry.node.id} entry={entry} currentId={currentId}
      current={entry.node.id === entries[currentIndex]?.node.id} rail={rail} />)}
    {overflow.length ? <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger className={cn(menuItemClass, "min-w-0 shrink", count === 0 && "font-semibold")}>
        <span className="truncate">{count === 0 ? label : t("chrome.more")}</span>
        <Glyph name="chevron-down" size={12} aria-hidden="true" />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Positioner side="bottom" align="start" sideOffset={4}>
          <DropdownMenu.Content>
            {overflow.map((entry) => entry.kind === "removed"
              ? <RemovedMenus key={entry.node.id} removed={[entry.node]} inMenu />
              : rail.menus(entry.node).length || rail.removedUnder(entry.node.id).some((node) => !node.app)
                ? <DropdownMenu.Group key={entry.node.id}>
                  <DropdownMenu.Label>{rail.label(entry.node)}</DropdownMenu.Label>
                  <MenuEntries item={entry.node} currentId={currentId} rail={rail} />
                </DropdownMenu.Group>
                : <MenuPage key={entry.node.id} item={entry.node} currentId={currentId} rail={rail} />)}
          </DropdownMenu.Content>
        </DropdownMenu.Positioner>
      </DropdownMenu.Portal>
    </DropdownMenu.Root> : null}
  </nav>;
}

type RemovedMenu = ReturnType<DeveloperRail["removedUnder"]>[number];
type AppMenuEntry = { kind: "menu"; node: ChromeMenuNode } | { kind: "removed"; node: RemovedMenu };

function AppMenuEntryControl({ entry, currentId, current, rail, measuring = false }: {
  entry: AppMenuEntry; currentId?: string; current: boolean; rail: DeveloperRail; measuring?: boolean;
}): ReactElement {
  if (entry.kind === "removed") return <RemovedMenus removed={[entry.node]} measuring={measuring} />;
  const item = entry.node;
  const children = rail.menus(item);
  const removed = rail.removedUnder(item.id).filter((node) => !node.app);
  const isLink = (!children.length && !removed.length) || (!item.to && children.length === 1 && !removed.length);
  const content = <>{rail.label(item)}{!isLink ? <Glyph name="chevron-down" size={12} aria-hidden="true" /> : null}</>;
  if (measuring) return <span className={menuItemClass}>{content}</span>;
  if (isLink) {
    const destination = children[0] ?? item;
    return <Tooltip label={rail.describe(destination)} side="bottom">
      <AppMenuLink to={destination.target} href={destination.target}
        data-current={destination.id === currentId} className={menuItemClass}>
        {content}
      </AppMenuLink>
    </Tooltip>;
  }
  return <DropdownMenu.Root modal={false}>
    <Tooltip label={rail.describe(item)} side="bottom">
      <DropdownMenu.Trigger aria-current={current ? "true" : undefined}
        data-current={current} className={menuItemClass}>{content}</DropdownMenu.Trigger>
    </Tooltip>
    <DropdownMenu.Portal>
      <DropdownMenu.Positioner side="bottom" align="start" sideOffset={4}>
        <DropdownMenu.Content>
          <MenuEntries item={item} currentId={currentId} rail={rail} />
        </DropdownMenu.Content>
      </DropdownMenu.Positioner>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>;
}

/** Developer mode: menus a layer removed, struck through where they showed, naming who removed them. */
function RemovedMenus({ removed, inMenu = false, measuring = false }: {
  removed: readonly RemovedMenu[]; inMenu?: boolean; measuring?: boolean;
}): ReactElement {
  const t = useUiT();
  return <>{removed.map((node) => {
    const label = t("developer.removedBy", { label: node.displayLabel, layer: node.by });
    if (measuring) return <span key={node.id} className={cn(menuItemClass, "line-through")}>{label}</span>;
    return inMenu
      ? <DropdownMenu.Item key={node.id} disabled className="line-through">{label}</DropdownMenu.Item>
      : <Tooltip key={node.id} label={node.route ? `${node.id} → ${node.route}` : node.id} side="bottom">
        <span tabIndex={0} role="link" aria-disabled="true" className={cn(menuItemClass, "cursor-default line-through")}>{label}</span>
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
    <RemovedMenus removed={rail.removedUnder(item.id).filter((node) => !node.app)} inMenu />
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

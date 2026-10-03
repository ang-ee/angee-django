import { useMemo, type ComponentPropsWithRef, type ReactElement } from "react";
import { createLink } from "@tanstack/react-router";

import { useHrefLinkOptions } from "../lib/in-app-link";
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
function AppMenuLink({ href, ...props }: ComponentPropsWithRef<"a"> & { "data-current"?: boolean }): ReactElement {
  const hrefOptions = useHrefLinkOptions(href);
  appMenuLink ??= createAppMenuLink();
  const Link = appMenuLink;
  return <Link {...props} {...hrefOptions} />;
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
  const settingsActive = match?.trail[0]?.group === "platform";
  const settings = useMemo(() => settingsActive ? tree.settingsEntry() : undefined, [tree, settingsActive]);
  const app = match?.app;
  const entries = useMemo<readonly AppMenuEntry[]>(() => [
    ...(settings?.items ?? (app ? rail.menus(app) : [])).map((node) => ({ kind: "menu" as const, node })),
    // Removed included apps belong to the rail, as before.
    ...(app ? removedMenusUnder(app.id, rail) : []).map((node) => ({ kind: "removed" as const, node })),
  ], [settings, app, rail]);
  const currentIndex = entries.findIndex((entry) => entry.kind === "menu" && match?.trail.some((node) => node.id === entry.node.id));
  const [containerRef, measurementRef, visible, overflow] = useOverflowCount(entries, currentIndex);
  if (!app && !settings) return null;
  const label = settings ? t("chrome.settings") : app!.displayLabel;
  const appTarget = settings?.target ?? app?.target;
  const currentId = match?.item.id;
  const overflowCurrent = visible.length === 0 && currentIndex >= 0;
  // The app's name titles its menus; it is not one of them, so it never carries the current mark.
  const title = <AppMenuLink href={appTarget}
    className="flex h-full max-w-48 shrink-0 items-center truncate rounded-4 text-15 font-semibold text-on-rail-hi no-underline outline-none focus-visible:focus-ring">
    {label}
  </AppMenuLink>;
  if (!entries.length) return title;

  return <>{title}<span aria-hidden="true" className="h-4 w-px shrink-0 bg-on-rail-mut/40" /><nav ref={containerRef} aria-label={t("chrome.appMenu", { label })}
    className={cn("relative flex h-full min-w-0 flex-1 items-center gap-1 overflow-hidden", className)}>
    {/* One intrinsic ordered list; inert and hidden so its copies are never navigable. */}
    <div ref={measurementRef} inert aria-hidden="true"
      className="pointer-events-none invisible absolute inset-y-0 left-0 flex w-max items-center gap-1">
      {entries.map((entry) => <div key={entry.node.id} className="flex h-full shrink-0">
        <AppMenuEntryControl entry={entry} currentId={currentId} current={false} rail={rail} measuring />
      </div>)}
      <span className={menuItemClass}>{t("chrome.more")}<Glyph name="chevron-down" size={12} aria-hidden="true" /></span>
    </div>
    {visible.map((index) => <AppMenuEntryControl key={entries[index]!.node.id} entry={entries[index]!} currentId={currentId}
      current={index === currentIndex} rail={rail} />)}
    {overflow.length ? <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger data-current={overflowCurrent} aria-current={overflowCurrent ? "true" : undefined}
        className={cn(menuItemClass, "min-w-0 shrink")}>
        <span className="truncate">{t("chrome.more")}</span>
        <Glyph name="chevron-down" size={12} aria-hidden="true" />
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Positioner side="bottom" align="start" sideOffset={4}>
          <DropdownMenu.Content>
            {overflow.map((index) => {
              const entry = entries[index]!;
              return entry.kind === "removed"
                ? <RemovedMenus key={entry.node.id} removed={[entry.node]} inMenu />
                : <MenuEntry key={entry.node.id} item={entry.node} currentId={currentId} rail={rail} />;
            })}
          </DropdownMenu.Content>
        </DropdownMenu.Positioner>
      </DropdownMenu.Portal>
    </DropdownMenu.Root> : null}
  </nav></>;
}

type RemovedMenu = ReturnType<DeveloperRail["removedUnder"]>[number];
type AppMenuEntry = { kind: "menu"; node: ChromeMenuNode } | { kind: "removed"; node: RemovedMenu };

function removedMenusUnder(parentId: string, rail: DeveloperRail): readonly RemovedMenu[] {
  return rail.removedUnder(parentId).filter((node) => !node.app);
}

/** A route-less menu with one child borrows its destination and keeps its own label. */
function menuShape(item: ChromeMenuNode, rail: DeveloperRail) {
  const children = rail.menus(item);
  const removed = removedMenusUnder(item.id, rail);
  const destination = !removed.length && (!children.length || (!item.to && children.length === 1))
    ? children[0] ?? item : undefined;
  return { children, removed, destination };
}

function AppMenuEntryControl({ entry, currentId, current, rail, measuring = false }: {
  entry: AppMenuEntry; currentId?: string; current: boolean; rail: DeveloperRail; measuring?: boolean;
}): ReactElement {
  if (entry.kind === "removed") return <RemovedMenus removed={[entry.node]} measuring={measuring} />;
  const item = entry.node;
  const shape = menuShape(item, rail);
  const content = <>{rail.label(item)}{!shape.destination ? <Glyph name="chevron-down" size={12} aria-hidden="true" /> : null}</>;
  if (measuring) return <span className={menuItemClass}>{content}</span>;
  if (shape.destination) {
    const destination = shape.destination;
    return <Tooltip label={rail.describe(destination)} side="bottom">
      <AppMenuLink href={destination.target}
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
          <MenuEntries item={item} shape={shape} currentId={currentId} rail={rail} />
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

/** A level-2 menu inside More: the row's link by the shared shape rule, otherwise a labelled group. */
function MenuEntry({ item, currentId, rail }: {
  item: ChromeMenuNode; currentId?: string; rail: DeveloperRail;
}): ReactElement {
  const shape = menuShape(item, rail);
  if (shape.destination) return <MenuPage target={shape.destination.target} label={rail.label(item)}
    current={shape.destination.id === currentId} description={rail.describe(shape.destination)} />;
  return <DropdownMenu.Group>
    <DropdownMenu.Label>{rail.label(item)}</DropdownMenu.Label>
    <MenuEntries item={item} shape={shape} currentId={currentId} rail={rail} />
  </DropdownMenu.Group>;
}

/** Deeper levels are labelled groups in the same native dropdown. */
function MenuEntries({ item, shape: { children, removed }, currentId, rail }: {
  item: ChromeMenuNode; shape: ReturnType<typeof menuShape>; currentId?: string; rail: DeveloperRail;
}): ReactElement {
  const ownPage = item.to && !children.some((child) => child.targetPath === item.path);
  return <>
    {ownPage ? <MenuPage target={item.target} label={rail.label(item)} current={item.id === currentId} description={rail.describe(item)} /> : null}
    {children.map((child) => {
      const shape = menuShape(child, rail);
      return shape.children.length || shape.removed.length ? <DropdownMenu.Group key={child.id}>
        {child.displayLabel !== item.displayLabel ? <DropdownMenu.Label>{rail.label(child)}</DropdownMenu.Label> : null}
        <MenuEntries item={child} shape={shape} currentId={currentId} rail={rail} />
      </DropdownMenu.Group> : <MenuPage key={child.id} target={child.target} label={rail.label(child)}
        current={child.id === currentId} description={rail.describe(child)} />;
    })}
    <RemovedMenus removed={removed} inMenu />
  </>;
}

function MenuPage({ target, label, current = false, description }: {
  target?: string; label: string; current?: boolean; description?: string;
}): ReactElement | null {
  if (!target) return null;
  return <DropdownMenu.LinkItem href={target} closeOnClick
    render={<AppMenuLink href={target} data-current={current} title={description} />}>
    {label}
  </DropdownMenu.LinkItem>;
}

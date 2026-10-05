import { Fragment, createContext, createElement, useContext, useMemo, useState, type ReactElement, type ReactNode } from "react";
import { useMenu, type TreeMenuItem } from "@refinedev/core";
import { useRouterState } from "@tanstack/react-router";

import { isMenuTone } from "../lib/tones";
import { useAppRuntime, useDeveloperMode } from "../runtime";
import {
  type ChromeMenuGroup,
  type ChromeMenuItem,
  type ChromeMenuNode,
  type ChromeMenuStatus,
  type ChromeMenuTone,
  MenuTree,
  type MenuMatch,
} from "./menu-tree";

interface RefineChromeMenuMeta {
  menuId?: unknown;
  /** Own resolved href, or null when Refine's list borrows a descendant target. */
  menuTarget?: unknown;
  parent?: unknown;
  menuParent?: unknown;
  menuOrder?: unknown;
  appRoot?: unknown;
  app?: unknown;
  icon?: unknown;
  description?: unknown;
  group?: unknown;
  status?: unknown;
  tone?: unknown;
  badge?: unknown;
  hidden?: unknown;
}

export function useChromeMenuItems(): readonly ChromeMenuItem[] {
  const { menuItems } = useMenu();
  return useMemo(
    () => chromeMenuItemsFromRefine(menuItems),
    [menuItems],
  );
}

export function useChromeMenuTree(): MenuTree {
  const items = useChromeMenuItems();
  return useMemo(() => MenuTree.from(items), [items]);
}

interface ChromePlace {
  tree: MenuTree;
  pathname: string;
  searchStr: string;
  match: MenuMatch | undefined;
  railPlace: ReturnType<MenuTree["railPlace"]>;
  /** Last committed location outside Settings, including search and hash; reloads forget it. */
  lastAppHref: string | null;
}

const ChromePlaceContext = createContext<ChromePlace | null>(null);

/** Share one full-tree match across the rail, drawer and top-bar app menu. */
export function ChromePlaceProvider({ menuItems, children }: {
  menuItems?: readonly ChromeMenuItem[] | MenuTree;
  children: ReactNode;
}): ReactElement {
  const inherited = useContext(ChromePlaceContext);
  return inherited && (menuItems === undefined || menuItems === inherited.tree)
    ? createElement(Fragment, null, children)
    : createElement(ChromePlaceOwner, { menuItems }, children);
}

function ChromePlaceOwner({ menuItems, children }: {
  menuItems?: readonly ChromeMenuItem[] | MenuTree;
  children?: ReactNode;
}): ReactElement {
  const runtimeTree = useChromeMenuTree();
  const tree = useMemo(() => MenuTree.from(menuItems ?? runtimeTree), [menuItems, runtimeTree]);
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const searchStr = useRouterState({ select: (state) => state.location.searchStr });
  const href = useRouterState({ select: (state) => state.location.href });
  // Developer mode's rail lists hidden apps, so the top bar follows into them.
  const developerMode = useDeveloperMode();
  const { activeMenuId } = useAppRuntime();
  const match = useMemo(() => tree.match(pathname, searchStr, developerMode, activeMenuId ?? undefined), [tree, pathname, searchStr, developerMode, activeMenuId]);
  const railPlace = useMemo(() => tree.railPlace(match, developerMode), [tree, match, developerMode]);
  const [lastAppHref, setLastAppHref] = useState<string | null>(null);
  if (railPlace.scope !== "settings" && lastAppHref !== href) setLastAppHref(href);
  const place = useMemo(() => ({ tree, pathname, searchStr, match, railPlace, lastAppHref }), [tree, pathname, searchStr, match, railPlace, lastAppHref]);
  return createElement(ChromePlaceContext.Provider, { value: place }, children);
}

export function useChromePlace(): ChromePlace {
  const place = useContext(ChromePlaceContext);
  if (!place) throw new Error("useChromePlace requires ChromePlaceProvider.");
  return place;
}

/** The console's place when one is provided; leaf chrome such as the breadcrumb also renders without it. */
export function useOptionalChromePlace(): ChromePlace | null {
  return useContext(ChromePlaceContext);
}

export function chromeMenuItemsFromRefine(
  menuItems: readonly TreeMenuItem[],
): readonly ChromeMenuItem[] {
  const order = new Map<string, number>();
  const items = menuItems.flatMap((item) => chromeMenuItemFromRefine(item, order));
  items.sort((left, right) => (order.get(left.id) ?? Infinity) - (order.get(right.id) ?? Infinity));
  const originals = new Map(items.map((item) => [item.id, item]));
  const nested = (node: ChromeMenuNode): ChromeMenuItem => ({
    ...originals.get(node.id)!,
    ...(node.children?.length ? { children: node.children.map(nested) } : {}),
  });
  return MenuTree.from(items).roots.map(nested);
}

function chromeMenuItemFromRefine(
  item: TreeMenuItem,
  order: Map<string, number>,
  inheritedParent?: string,
): readonly ChromeMenuItem[] {
  const meta = chromeMenuMeta(item);
  const id = stringValue(meta.menuId) ?? item.identifier ?? item.name;
  const position = numberValue(meta.menuOrder);
  if (position !== undefined) order.set(id, position);
  const label = stringValue(item.label) ?? stringValue(meta.menuId) ?? item.name;
  const target = meta.menuTarget === undefined ? item.route : stringValue(meta.menuTarget);
  const parentId = menuParentId(meta.menuParent ?? meta.parent) ?? inheritedParent;
  const children = item.children.flatMap((child) => chromeMenuItemFromRefine(child, order, id));
  const menuItem: ChromeMenuItem = {
    id,
    label,
    ...(target ? { to: target } : {}),
    ...(parentId ? { parentId } : {}),
    ...(meta.appRoot === true ? { appRoot: true } : {}),
    ...(meta.app === true ? { app: true } : {}),
    ...(stringValue(meta.icon ?? item.icon) ? { icon: stringValue(meta.icon ?? item.icon) } : {}),
    ...(stringValue(meta.description) ? { description: stringValue(meta.description) } : {}),
    ...(menuGroup(meta.group) ? { group: menuGroup(meta.group) } : {}),
    ...(menuStatus(meta.status) ? { status: menuStatus(meta.status) } : {}),
    ...(menuTone(meta.tone) ? { tone: menuTone(meta.tone) } : {}),
    ...(numberValue(meta.badge) !== undefined ? { badge: numberValue(meta.badge) } : {}),
    ...(meta.hidden === true ? { hidden: true } : {}),
  };
  return [menuItem, ...children];
}

function chromeMenuMeta(item: TreeMenuItem): RefineChromeMenuMeta {
  return (item.meta ?? {}) as RefineChromeMenuMeta;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function menuParentId(value: unknown): string | undefined {
  const parent = stringValue(value);
  if (!parent) return undefined;
  return parent.startsWith("menu:") ? parent.slice("menu:".length) : parent;
}

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function menuGroup(value: unknown): ChromeMenuGroup | undefined {
  return value === "domain" || value === "platform" ? value : undefined;
}

function menuStatus(value: unknown): ChromeMenuStatus | undefined {
  return value === "active" || value === "future" ? value : undefined;
}

function menuTone(value: unknown): ChromeMenuTone | undefined {
  return isMenuTone(value) ? value : undefined;
}

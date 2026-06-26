import { useMemo } from "react";
import { useMenu, type TreeMenuItem } from "@refinedev/core";

import { isMenuTone } from "../lib/tones";
import {
  type ChromeMenuGroup,
  type ChromeMenuItem,
  type ChromeMenuStatus,
  type ChromeMenuTone,
  MenuTree,
} from "./menu-tree";

interface RefineChromeMenuMeta {
  menuId?: unknown;
  parent?: unknown;
  appRoot?: unknown;
  icon?: unknown;
  description?: unknown;
  group?: unknown;
  sidebar?: unknown;
  status?: unknown;
  tone?: unknown;
  badge?: unknown;
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

export function chromeMenuItemsFromRefine(
  menuItems: readonly TreeMenuItem[],
): readonly ChromeMenuItem[] {
  return menuItems.flatMap((item) => chromeMenuItemFromRefine(item));
}

function chromeMenuItemFromRefine(
  item: TreeMenuItem,
): readonly ChromeMenuItem[] {
  const meta = chromeMenuMeta(item);
  const id = stringValue(meta.menuId) ?? item.identifier ?? item.name;
  const label = stringValue(item.label) ?? stringValue(meta.menuId) ?? item.name;
  const parentId = menuParentId(meta.parent);
  const children = item.children.flatMap((child) => chromeMenuItemFromRefine(child));
  const menuItem: ChromeMenuItem = {
    id,
    label,
    ...(item.route ? { to: item.route } : {}),
    ...(parentId ? { parentId } : {}),
    ...(meta.appRoot === true ? { appRoot: true } : {}),
    ...(stringValue(meta.icon ?? item.icon) ? { icon: stringValue(meta.icon ?? item.icon) } : {}),
    ...(stringValue(meta.description) ? { description: stringValue(meta.description) } : {}),
    ...(menuGroup(meta.group) ? { group: menuGroup(meta.group) } : {}),
    ...(typeof meta.sidebar === "boolean" ? { sidebar: meta.sidebar } : {}),
    ...(menuStatus(meta.status) ? { status: menuStatus(meta.status) } : {}),
    ...(menuTone(meta.tone) ? { tone: menuTone(meta.tone) } : {}),
    ...(numberValue(meta.badge) !== undefined ? { badge: numberValue(meta.badge) } : {}),
    ...(children.length ? { children } : {}),
  };
  return [menuItem];
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

import type { MenuItem } from "@angee/sdk";

export type ChromeMenuGroup = "domain" | "platform";
export type ChromeMenuStatus = "active" | "future";
export type ChromeMenuTone =
  | "brand"
  | "danger"
  | "info"
  | "muted"
  | "success"
  | "warning";

export interface ChromeMenuItem extends MenuItem {
  children?: readonly ChromeMenuItem[];
  parent?: string;
  parentId?: string;
  description?: string;
  group?: ChromeMenuGroup;
  status?: ChromeMenuStatus;
  tone?: ChromeMenuTone;
  badge?: number;
}

export interface MenuTree {
  roots: readonly ChromeMenuItem[];
  byId: ReadonlyMap<string, ChromeMenuItem>;
}

type MutableChromeMenuItem = Omit<ChromeMenuItem, "children"> & {
  children?: MutableChromeMenuItem[];
};

const CHROME_MENU_PARENT_IDS = new Set(["systray", "user"]);

export function buildMenuTree(
  items: readonly ChromeMenuItem[],
): MenuTree {
  const byId = new Map<string, MutableChromeMenuItem>();
  const childIds = new Set<string>();
  const ordered: MutableChromeMenuItem[] = [];

  for (const item of items) {
    const clone = cloneMenuItem(item);
    byId.set(clone.id, clone);
    ordered.push(clone);
  }

  for (const item of items) {
    const clone = byId.get(item.id);
    if (!clone || !item.children?.length) continue;
    clone.children = item.children.map((child) => {
      const childClone = cloneMenuItem(child);
      byId.set(childClone.id, childClone);
      childIds.add(childClone.id);
      return childClone;
    });
  }

  for (const item of ordered) {
    const parentId = item.parentId ?? item.parent;
    if (!parentId) continue;
    const parent = byId.get(parentId);
    if (!parent) continue;
    parent.children = [...(parent.children ?? []), item];
    childIds.add(item.id);
  }

  return {
    byId,
    roots: ordered.filter((item) => {
      if (childIds.has(item.id)) return false;
      return !menuParentId(item);
    }),
  };
}

export function railMenuItems(
  itemsOrTree: readonly ChromeMenuItem[] | MenuTree,
): readonly ChromeMenuItem[] {
  const tree = isMenuTree(itemsOrTree)
    ? itemsOrTree
    : buildMenuTree(itemsOrTree);
  return tree.roots.filter((item) => {
    if (CHROME_MENU_PARENT_IDS.has(item.id)) return false;
    return Boolean(menuItemTarget(item));
  });
}

export function topMenuItems(
  itemsOrTree: readonly ChromeMenuItem[] | MenuTree,
): readonly ChromeMenuItem[] {
  return railMenuItems(itemsOrTree);
}

export function menuItemTarget(item: ChromeMenuItem): string | undefined {
  return item.to ?? item.children?.find((child) => child.to)?.to;
}

export function menuItemLabel(item: ChromeMenuItem): string {
  return item.label ?? titleCase(item.id);
}

export function menuItemIcon(item: ChromeMenuItem): string {
  return item.icon ?? item.id;
}

export function menuItemMatchesPath(
  item: ChromeMenuItem,
  pathname: string,
): boolean {
  const target = menuItemTarget(item);
  if (!target || target === "#") return false;
  return pathname === target || pathname.startsWith(`${target}/`);
}

function cloneMenuItem(item: ChromeMenuItem): MutableChromeMenuItem {
  const { children: _children, ...clone } = item;
  return clone;
}

function menuParentId(item: ChromeMenuItem): string | undefined {
  return item.parentId ?? item.parent;
}

function isMenuTree(value: readonly ChromeMenuItem[] | MenuTree): value is MenuTree {
  return "roots" in value;
}

function titleCase(value: string): string {
  return value
    .split(/[-_.\s]+/)
    .filter(Boolean)
    .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

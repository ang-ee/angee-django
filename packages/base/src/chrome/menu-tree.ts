import type { ComposedMenuItem, MenuItem } from "@angee/sdk";

import { titleCase } from "../lib/titleCase";

export type ChromeMenuGroup = "domain" | "platform";
export type ChromeMenuStatus = "active" | "future";
export type ChromeMenuTone =
  | "brand"
  | "danger"
  | "info"
  | "neutral"
  | "success"
  | "warning";

export interface BaseMenuItem extends MenuItem {
  children?: readonly BaseMenuItem[];
  parent?: string;
  parentId?: string;
  description?: string;
  group?: ChromeMenuGroup;
  sidebar?: boolean;
  status?: ChromeMenuStatus;
  tone?: ChromeMenuTone;
  badge?: number;
}

export interface ChromeMenuItem extends ComposedMenuItem {
  children?: readonly ChromeMenuItem[];
  parent?: string;
  parentId?: string;
  description?: string;
  group?: ChromeMenuGroup;
  sidebar?: boolean;
  status?: ChromeMenuStatus;
  tone?: ChromeMenuTone;
  badge?: number;
}

/**
 * Whether `pathname` is `target` or nests under it (`target/…`). The one
 * path-match predicate shared by `ChromeMenuNode.matchesPath` and the app
 * chooser; a missing or `#` target never matches.
 */
export function pathMatchesTarget(
  pathname: string,
  target: string | undefined,
): boolean {
  if (!target || target === "#") return false;
  return pathname === target || pathname.startsWith(`${target}/`);
}

export class ChromeMenuNode implements ChromeMenuItem {
  id: string;
  label?: string;
  route?: string;
  to?: string;
  icon?: string;
  children?: readonly ChromeMenuNode[];
  parent?: string;
  parentId?: string;
  parentNode?: ChromeMenuNode;
  description?: string;
  group?: ChromeMenuGroup;
  sidebar?: boolean;
  status?: ChromeMenuStatus;
  tone?: ChromeMenuTone;
  badge?: number;

  constructor(item: ChromeMenuItem) {
    const { children: _children, ...clone } = item;
    Object.assign(this, clone);
    this.id = item.id;
  }

  get target(): string | undefined {
    return this.resolveTarget(new Set());
  }

  get displayLabel(): string {
    return this.label ?? titleCase(this.id);
  }

  get iconName(): string {
    return this.icon ?? this.id;
  }

  get parentKey(): string | undefined {
    return this.parentId ?? this.parent;
  }

  get targetedChildren(): readonly ChromeMenuNode[] {
    return (this.children ?? []).filter((child) => child.target);
  }

  matchesPath(pathname: string): boolean {
    return pathMatchesTarget(pathname, this.target);
  }

  /** True when this node's own target, or any immediate child's, matches `pathname`. */
  isActive(pathname: string): boolean {
    return this.matchesPath(pathname) || this.hasActiveDescendant(pathname);
  }

  /** True when an immediate child's target matches `pathname`. */
  hasActiveDescendant(pathname: string): boolean {
    return Boolean(this.children?.some((child) => child.matchesPath(pathname)));
  }

  appendChild(child: ChromeMenuNode): void {
    child.parentNode = this;
    this.children = [...(this.children ?? []), child];
  }

  private resolveTarget(visited: Set<string>): string | undefined {
    if (visited.has(this.id)) {
      throw new Error(`Menu item "${this.id}" creates a target cycle.`);
    }
    visited.add(this.id);
    try {
      if (this.to) return this.to;
      for (const child of this.children ?? []) {
        const target = child.resolveTarget(visited);
        if (target) return target;
      }
      return undefined;
    } finally {
      visited.delete(this.id);
    }
  }
}

export class MenuTree {
  readonly roots: readonly ChromeMenuNode[];
  readonly byId: ReadonlyMap<string, ChromeMenuNode>;

  constructor(
    roots: readonly ChromeMenuNode[],
    byId: ReadonlyMap<string, ChromeMenuNode>,
  ) {
    this.roots = roots;
    this.byId = byId;
  }

  static from(itemsOrTree: readonly ChromeMenuItem[] | MenuTree): MenuTree {
    return itemsOrTree instanceof MenuTree
      ? itemsOrTree
      : buildMenuTree(itemsOrTree);
  }

  railMenuItems(): readonly ChromeMenuNode[] {
    return this.roots.filter((item) => {
      if (CHROME_MENU_PARENT_IDS.has(item.id)) return false;
      return Boolean(item.target);
    });
  }

  /**
   * Every navigable destination for the command palette: each leaf carrying its
   * own resolved `target`, paired with its root ancestor (so the palette groups
   * by app). Parents that only borrow a child's target are skipped — their
   * leaves carry the real destinations — as are the chrome action menus
   * (systray/user) and their entries. Build-order deterministic (`byId`).
   */
  navigableItems(): readonly {
    item: ChromeMenuNode;
    root: ChromeMenuNode;
    target: string;
  }[] {
    const result: { item: ChromeMenuNode; root: ChromeMenuNode; target: string }[] = [];
    for (const node of this.byId.values()) {
      if (CHROME_MENU_PARENT_IDS.has(node.id)) continue;
      const target = node.target;
      if (!target || target === "#") continue;
      if (node.targetedChildren.length) continue;
      const root = this.trailFor(node.id)[0];
      if (root && CHROME_MENU_PARENT_IDS.has(root.id)) continue;
      result.push({ item: node, root: root ?? node, target });
    }
    return result;
  }

  /**
   * The active app's section links for the top bar: the children of the root
   * the current path belongs to, rendered flat. Apps live in the rail /
   * app-switcher; the top bar navigates *within* the active app, so a sibling
   * app's sections never leak here. A single-page app (a root with no children,
   * e.g. Notes) contributes nothing.
   */
  appSectionItems(pathname: string): readonly ChromeMenuNode[] {
    const active = this.activeAppRoot(pathname);
    return active?.targetedChildren ?? [];
  }

  /**
   * The root the current path belongs to — the app whose own target or a
   * child's target is the longest prefix of `pathname` (most-specific wins).
   */
  activeAppRoot(pathname: string): ChromeMenuNode | undefined {
    let best: ChromeMenuNode | undefined;
    let bestLength = -1;
    for (const root of this.roots) {
      for (const candidate of [root, ...(root.children ?? [])]) {
        const target = candidate.target;
        if (!target || !candidate.matchesPath(pathname)) continue;
        if (target.length > bestLength) {
          best = root;
          bestLength = target.length;
        }
      }
    }
    return best;
  }

  /** Ancestor stack from root to `itemId`; throws if parent links cycle. */
  trailFor(itemId: string): readonly ChromeMenuNode[] {
    const item = this.byId.get(itemId);
    if (!item) return [];
    const trail: ChromeMenuNode[] = [];
    const visited = new Set<string>();
    let current: ChromeMenuNode | undefined = item;
    while (current) {
      if (visited.has(current.id)) {
        throw new Error(`Menu item "${current.id}" creates a parent cycle.`);
      }
      visited.add(current.id);
      trail.push(current);
      current = current.parentNode;
    }
    return trail.reverse();
  }

  /** Menu nodes whose `route` ref points at `routeName`, in tree insertion order. */
  itemsForRoute(routeName: string): readonly ChromeMenuNode[] {
    return [...this.byId.values()].filter((item) => item.route === routeName);
  }
}

const CHROME_MENU_PARENT_IDS = new Set(["systray", "user"]);

export function buildMenuTree(
  items: readonly ChromeMenuItem[],
): MenuTree {
  const byId = new Map<string, ChromeMenuNode>();
  const childIds = new Set<string>();

  function cloneMenuItem(
    item: ChromeMenuItem,
    parent?: ChromeMenuNode,
  ): ChromeMenuNode {
    const clone = new ChromeMenuNode(item);
    if (byId.has(clone.id)) {
      throw new Error(`Menu item "${clone.id}" is declared more than once.`);
    }
    clone.parentNode = parent;
    byId.set(clone.id, clone);
    if (parent) childIds.add(clone.id);
    if (item.children?.length) {
      clone.children = item.children.map((child) => cloneMenuItem(child, clone));
    }
    return clone;
  }

  const ordered = items.map((item) => cloneMenuItem(item));

  for (const item of ordered) {
    const parentId = item.parentKey;
    if (!parentId) continue;
    const parent = byId.get(parentId);
    if (!parent) {
      // A `parentId` is an explicit contribution into another addon's menu, so a
      // missing target is a wiring bug — fail fast (matching the duplicate-id and
      // cycle throws), except for the reserved virtual chrome anchors.
      if (CHROME_MENU_PARENT_IDS.has(parentId)) continue;
      throw new Error(`Menu item "${item.id}" names unknown parent "${parentId}".`);
    }
    parent.appendChild(item);
    childIds.add(item.id);
  }

  const tree = new MenuTree(
    ordered.filter((item) => {
      if (childIds.has(item.id)) return false;
      return !item.parentKey;
    }),
    byId,
  );

  validateMenuTree(tree);

  return tree;
}

function validateMenuTree(tree: MenuTree): void {
  for (const item of tree.byId.values()) {
    void tree.trailFor(item.id);
    void item.target;
  }
}

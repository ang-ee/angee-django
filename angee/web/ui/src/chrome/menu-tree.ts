import type { ComposedMenuItem, MenuItem } from "../runtime";

import { titleCase } from "../lib/titleCase";
import type { Tone } from "../lib/tones";

export type ChromeMenuGroup = "domain" | "platform";
export type ChromeMenuStatus = "active" | "future";
export const SETTINGS_MENU_ENTRY_DESCRIPTOR = {
  id: "settings",
  group: "platform",
  icon: "settings",
  tone: "neutral",
} as const;
/** The tones a chrome menu item may carry — the curated `MENU_TONES` slice of the
 *  `Tone` owner (`lib/tones.ts`). `Extract` keeps the nav-tone narrowing rather
 *  than widening to the full palette. */
export type ChromeMenuTone = Extract<
  Tone,
  "brand" | "danger" | "info" | "neutral" | "success" | "warning"
>;

/**
 * The chrome-extension fields layered onto a menu item by both rendered surfaces.
 * One owner for the fields shared by `BaseMenuItem` and `ChromeMenuItem`;
 * `children` is excluded because its element type differs per surface.
 */
export interface ChromeMenuExtra {
  parent?: string;
  parentId?: string;
  appRoot?: boolean;
  description?: string;
  group?: ChromeMenuGroup;
  status?: ChromeMenuStatus;
  tone?: ChromeMenuTone;
  badge?: number;
}

export interface BaseMenuItem extends MenuItem, ChromeMenuExtra {
  children?: readonly BaseMenuItem[];
}

export interface ChromeMenuItem extends ComposedMenuItem, ChromeMenuExtra {
  children?: readonly ChromeMenuItem[];
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
  appRoot?: boolean;
  description?: string;
  group?: ChromeMenuGroup;
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

  /** Most-specific targeted child whose subtree contains `pathname`. */
  activeTargetedChild(pathname: string): ChromeMenuNode | undefined {
    return deepestTargetMatch(this.targetedChildren, pathname);
  }

  matchesPath(pathname: string): boolean {
    return pathMatchesTarget(pathname, this.target);
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
    const targetedRoots = this.roots.filter((item) => {
      if (CHROME_MENU_PARENT_IDS.has(item.id)) return false;
      if (item.group === "platform") return false;
      return Boolean(item.target);
    });
    const appRoots = targetedRoots.filter((item) => item.appRoot);
    return appRoots.length ? appRoots : targetedRoots;
  }

  /** Navigable root categories that live in the Settings place. */
  settingsMenuItems(): readonly ChromeMenuNode[] {
    return this.roots.filter((item) => {
      if (CHROME_MENU_PARENT_IDS.has(item.id)) return false;
      return item.group === "platform" && Boolean(item.target);
    });
  }

  /** The one synthetic navigation entry that represents all platform roots. */
  settingsEntry(): {
    id: typeof SETTINGS_MENU_ENTRY_DESCRIPTOR.id;
    group: typeof SETTINGS_MENU_ENTRY_DESCRIPTOR.group;
    icon: typeof SETTINGS_MENU_ENTRY_DESCRIPTOR.icon;
    tone: typeof SETTINGS_MENU_ENTRY_DESCRIPTOR.tone;
    target: string;
    items: readonly ChromeMenuNode[];
  } | undefined {
    const items = this.settingsMenuItems();
    const target = items[0]?.target;
    return target
      ? { ...SETTINGS_MENU_ENTRY_DESCRIPTOR, target, items }
      : undefined;
  }

  /** Whether the current path belongs to a root in the Settings place. */
  isSettingsActive(pathname: string): boolean {
    return this.activeAppRoot(pathname)?.group === "platform";
  }

  /**
   * The rail's place for the current path — the one answer every chrome
   * surface asks instead of recombining `isSettingsActive`/`activeAppRoot`/
   * root lists itself: which scope is active, which roots that scope shows,
   * and which of them is the active one (`null` when the path belongs to
   * neither, or to the other scope's roots).
   */
  railPlace(pathname: string): {
    scope: "apps" | "settings";
    roots: readonly ChromeMenuNode[];
    activeRootId: string | null;
  } {
    const scope = this.isSettingsActive(pathname) ? "settings" : "apps";
    const roots = scope === "settings"
      ? this.settingsMenuItems()
      : this.railMenuItems();
    const active = this.activeAppRoot(pathname);
    return {
      scope,
      roots,
      activeRootId: roots.some((item) => item.id === active?.id)
        ? active?.id ?? null
        : null,
    };
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
   * The root the current path belongs to — the app whose own target or a
   * child's target is the longest prefix of `pathname` (most-specific wins).
   */
  activeAppRoot(pathname: string): ChromeMenuNode | undefined {
    return deepestTargetMatch(this.roots, pathname);
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

function* menuNodeDescendants(
  root: ChromeMenuNode,
): Generator<ChromeMenuNode> {
  yield root;
  for (const child of root.children ?? []) {
    yield* menuNodeDescendants(child);
  }
}

/** Most-specific matching subtree, returning the root that owns the match. */
function deepestTargetMatch<T extends ChromeMenuNode>(
  roots: readonly T[],
  pathname: string,
): T | undefined {
  let best: T | undefined;
  let bestLength = -1;
  for (const root of roots) {
    for (const candidate of menuNodeDescendants(root)) {
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
    if (clone.id === SETTINGS_MENU_ENTRY_DESCRIPTOR.id) {
      throw new Error(
        `Menu item "${clone.id}" uses the reserved Settings place id.`,
      );
    }
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

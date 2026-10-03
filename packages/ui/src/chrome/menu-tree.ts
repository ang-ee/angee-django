import type { ComposedMenuItem, MenuItem } from "../runtime";
import {
  UnknownRouteError,
  type RouteHref,
  type RouteHrefParams,
} from "../runtime/route-href";

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
  parentId?: string;
  appRoot?: boolean;
  description?: string;
  group?: ChromeMenuGroup;
  status?: ChromeMenuStatus;
  tone?: ChromeMenuTone;
  badge?: number;
  /** Left out of the rail and Settings; it still owns its routes and stays in the palette. */
  hidden?: boolean;
}

export interface BaseMenuItem extends MenuItem, ChromeMenuExtra {
  children?: readonly BaseMenuItem[];
}

export interface ChromeMenuItem extends ComposedMenuItem, ChromeMenuExtra {
  /** Compiler-emitted identity of an included, non-flattened app; not authorable. */
  app?: boolean;
  children?: readonly ChromeMenuItem[];
}

/**
 * Whether `pathname` is `target` or nests under it (`target/…`). The one
 * path-match predicate used by the menu matcher and explicit chooser links;
 * a missing or `#` target never matches.
 */
export function pathMatchesTarget(
  pathname: string,
  target: string | undefined,
): boolean {
  if (!target || target === "#") return false;
  if (isExternalTarget(target)) return false;
  const path = new URL(target, "https://angee.invalid").pathname;
  return pathname === path || pathname.startsWith(`${path}/`);
}

/** Resolve authored route targets once, before the chrome builds its menu tree. */
export function resolveMenuRouteTargets(
  items: readonly ComposedMenuItem[],
  routeHref: RouteHref,
): readonly ComposedMenuItem[] {
  return items.map((item) => resolveMenuRouteTarget(item, routeHref));
}

function resolveMenuRouteTarget(
  item: ComposedMenuItem,
  routeHref: RouteHref,
): ComposedMenuItem {
  if (item.route && item.to !== undefined) {
    throw new Error(
      `Menu item "${item.id}" declares both route and to; use exactly one target owner.`,
    );
  }
  if (!item.route && item.params !== undefined) {
    throw new Error(
      `Menu item "${item.id}" declares params without a route.`,
    );
  }
  if (item.to !== undefined && !isExternalTarget(item.to)) {
    throw new Error(
      `Menu item "${item.id}" declares internal target "${item.to}" as to; use route and params.`,
    );
  }

  let routePath: string | undefined;
  if (item.route) {
    try {
      routePath = routeHref(item.route, item.params, item.defaultResourceView ? { preset: item.defaultResourceView } : undefined);
    } catch (error) {
      if (error instanceof UnknownRouteError) {
        throw new Error(
          `Menu item "${item.id}" references unknown route "${item.route}".`,
        );
      }
      if (error instanceof Error) {
        throw new Error(
          `Menu item "${item.id}" cannot resolve its route: ${error.message}`,
        );
      }
      throw error;
    }
  }
  return {
    ...item,
    to: routePath ?? item.to,
    children: item.children
      ? resolveMenuRouteTargets(item.children, routeHref)
      : item.children,
  };
}

function isExternalTarget(target: string): boolean {
  return target.startsWith("//") || /^[A-Za-z][A-Za-z\d+.-]*:/.test(target);
}

export class ChromeMenuNode implements ChromeMenuItem {
  id: string;
  label?: string;
  route?: string;
  params?: RouteHrefParams;
  defaultResourceView?: string;
  to?: string;
  /** Parsed once; `to` retains the full href used by navigation. */
  path?: string;
  search?: string;
  icon?: string;
  children?: readonly ChromeMenuNode[];
  parentId?: string;
  parentNode?: ChromeMenuNode;
  appRoot?: boolean;
  app?: boolean;
  description?: string;
  group?: ChromeMenuGroup;
  status?: ChromeMenuStatus;
  tone?: ChromeMenuTone;
  badge?: number;
  hidden?: boolean;

  constructor(item: ChromeMenuItem) {
    const { children: _children, ...clone } = item;
    Object.assign(this, clone);
    this.id = item.id;
    if (item.to && item.to !== "#" && !isExternalTarget(item.to)) {
      const url = new URL(item.to, "https://angee.invalid");
      this.path = url.pathname;
      this.search = url.search;
    }
  }

  get target(): string | undefined {
    return this.resolveTarget(new Set());
  }

  get targetPath(): string | undefined {
    return this.path ?? this.children?.find((child) => child.targetPath)?.targetPath;
  }

  get displayLabel(): string {
    return this.label ?? titleCase(this.id);
  }

  get iconName(): string {
    return this.icon ?? this.id;
  }

  get parentKey(): string | undefined {
    return this.parentId;
  }

  get isApp(): boolean {
    return (!this.parentNode && this.group !== "platform") || this.app === true;
  }

  /** Visible included apps, whose own menus belong in the top bar. */
  appChildren(): readonly ChromeMenuNode[] {
    return this.targetedChildren.filter((child) => child.isApp);
  }

  /** Visible menu children, excluding included apps. */
  menuItems(): readonly ChromeMenuNode[] {
    return this.targetedChildren.filter((child) => !child.isApp);
  }

  /** Visible children with navigation targets, before app/menu classification. */
  get targetedChildren(): readonly ChromeMenuNode[] {
    return this.railChildren();
  }

  /** Children with a target; developer mode's rail includes the hidden ones. */
  railChildren(includeHidden = false): readonly ChromeMenuNode[] {
    return (this.children ?? []).filter((child) => child.target && (includeHidden || !child.hidden));
  }

  /** Most-specific rail child whose subtree contains `pathname`. */
  activeTargetedChild(pathname: string, includeHidden = false): ChromeMenuNode | undefined {
    return matchWithin(this.railChildren(includeHidden), pathname)?.trail[0];
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
      const children = this.children ?? [];
      for (const child of [...children.filter((item) => !item.hidden), ...children.filter((item) => item.hidden)]) {
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

  /** Project one host-selected root, retaining contributed descendants. */
  confineTo(rootId: string): MenuTree {
    const root = this.roots.find((item) => item.id === rootId);
    if (!root) throw new Error(`Unknown menu root "${rootId}" in confineTo.`);
    const settings: ChromeMenuItem[] = [];
    const project = (item: ChromeMenuNode): ChromeMenuItem => ({
      ...item,
      children: item.children?.flatMap((child) => {
        if (child.group !== "platform") return [project(child)];
        settings.push({ ...child, parentId: undefined, appRoot: false });
        return [];
      }),
    });
    const app = { ...project(root), appRoot: true, group: "domain" as const };
    return MenuTree.from([app, ...settings]);
  }

  /** Explicit app roots win; without an opt-in every root remains an app. */
  appRoots(): readonly ChromeMenuNode[] {
    return this.roots.some((item) => item.appRoot === true)
      ? this.roots.filter((item) => item.appRoot === true)
      : this.roots;
  }

  /** The app roots the rail lists; developer mode's rail includes the hidden ones. */
  railMenuItems(includeHidden = false): readonly ChromeMenuNode[] {
    return this.appRoots().filter((item) => {
      if (CHROME_MENU_PARENT_IDS.has(item.id) || (item.hidden && !includeHidden)) return false;
      if (item.group === "platform") return false;
      return Boolean(item.target);
    });
  }

  /** Navigable root categories that live in the Settings place. */
  settingsMenuItems(includeHidden = false): readonly ChromeMenuNode[] {
    return this.roots.filter((item) => {
      if (CHROME_MENU_PARENT_IDS.has(item.id) || (item.hidden && !includeHidden)) return false;
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
  railPlace(location: string | MenuMatch | undefined, includeHidden = false): {
    scope: "apps" | "settings";
    roots: readonly ChromeMenuNode[];
    activeRootId: string | null;
  } {
    const active = (typeof location === "string" ? this.match(location) : location)?.trail[0];
    const scope = active?.group === "platform" ? "settings" : "apps";
    const roots = scope === "settings"
      ? this.settingsMenuItems(includeHidden)
      : this.railMenuItems(includeHidden);
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
    return this.match(pathname)?.trail[0];
  }

  /** One highlighted destination; ancestors remain expanded, not selected. */
  activeItem(pathname: string): ChromeMenuNode | undefined {
    return this.match(pathname)?.item;
  }

  /** Own-path matches rank by length, equal params, fewer mismatches, depth, then pre-order. */
  match(path: string, search?: string | URLSearchParams): MenuMatch | undefined {
    return matchWithin(this.roots, path, search);
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

export interface MenuMatch {
  item: ChromeMenuNode;
  trail: readonly ChromeMenuNode[];
  /** Nearest visible app on the navigation trail. Settings has its own place. */
  app?: ChromeMenuNode;
}

function matchWithin(
  roots: readonly ChromeMenuNode[],
  path: string,
  search?: string | URLSearchParams,
): MenuMatch | undefined {
  const location = new URL(path, "https://angee.invalid");
  const params = new URLSearchParams(search ?? location.search);
  let best: { item: ChromeMenuNode; trail: readonly ChromeMenuNode[] } | undefined;
  let bestRank = [-1, -1, -Infinity, -1];
  const visit = (item: ChromeMenuNode, ancestors: readonly ChromeMenuNode[]): void => {
    const trail = [...ancestors, item];
    if (item.path && pathMatchesTarget(location.pathname, item.path)) {
      const targetParams = [...new URLSearchParams(item.search)];
      const equalParams = targetParams.filter(([key, value]) => params.getAll(key).includes(value)).length;
      const mismatches = targetParams.length - equalParams;
      const rank = [item.path.length, equalParams, -mismatches, trail.length];
      const firstDifference = rank.findIndex((value, index) => value !== bestRank[index]);
      if (firstDifference !== -1 && rank[firstDifference]! > bestRank[firstDifference]!) {
        best = { item, trail };
        bestRank = rank;
      }
    }
    for (const child of item.children ?? []) visit(child, trail);
  };
  for (const root of roots) visit(root, []);
  return best && { ...best, app: best.trail.findLast((item) => item.isApp && !item.hidden) };
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
    if (item.appRoot !== undefined && (item.parentNode || item.parentKey)) {
      throw new Error(`Menu item "${item.id}" declares appRoot on a non-root item.`);
    }
    if (item.appRoot === true && !item.target) {
      throw new Error(`Menu item "${item.id}" declares appRoot without a target.`);
    }
    void tree.trailFor(item.id);
    void item.target;
  }
}

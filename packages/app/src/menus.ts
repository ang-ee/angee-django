import type { BaseMenuItem, ChromeMenuExtra, ChromeMenuItem } from "@angee/ui/chrome/menu-tree";
import type { HiddenMenuItem, MenuItem, RemovedMenuItem } from "@angee/ui/runtime";

import { positionSiblings } from "@angee/ui/lib/position";

import type { AddonRoute } from "./define-addon";
import { DEPLOYMENT_LAYER_ID, assertMayAlter, layerAncestry, overridesField, type Layer } from "./layers";
import { normalizeRoutePath } from "./route-paths";

/** An included node, optionally rendered flat into the including node. */
export type MenuInclude = string | { id: string; flatten?: boolean };

/**
 * One key of an addon's `menus` dict. A key in the addon's own namespace (its
 * id, or `<id>.…`) declares a node; any other key alters a node declared by an
 * addon this one depends on. A node targets at most one of `route` (a link),
 * `mount` (a borrowed page) and `to` (an external URL).
 */
export interface MenuEntry extends Omit<MenuItem, "id" | "children">, Omit<ChromeMenuExtra, "parentId" | "badge" | "hidden"> {
  /** The parent node id; `null` places the node at the top of the rail. */
  parent?: string | null;
  /**
   * Borrow a page: a route of this addon or one it depends on, aliased under the
   * node's app as a route named after the node (`<id>`, and `<id>.record` for the
   * route's record child) that reuses the route's page, model and detail. The
   * source app keeps its own page; the node's `defaultResourceView` and
   * `recordMatch` go onto the alias.
   */
  mount?: string;
  /**
   * The URL path mounts use. On an app (a node declared at the top of the rail)
   * it is the base its mounts live under, `/<id>` by default; on a mount it is
   * the alias's path under that base.
   */
  path?: string;
  /** A mount's claim on the records whose `field` holds `equals`: they open in it from every app. */
  recordMatch?: NonNullable<AddonRoute["recordMatch"]>;
  sequence?: number;
  /** Place the node right before or after a node that ends up beside it; a cycle fails composition. */
  before?: string;
  after?: string;
  /** Nodes that become children of this one, with their subtrees. */
  include?: readonly MenuInclude[];
  /** Structural removal: the node and its subtree leave, and pages only they reach become unavailable. */
  remove?: boolean;
  /** Cosmetic: the node leaves the rail but keeps its pages reachable; `false` shows it again. */
  hide?: boolean;
  /** Allowlist of the children the rail shows; layers narrow it, never widen it. */
  only?: readonly string[];
  /** The deployment only (`ANGEE_UI.menus`), beside an `only`: it stands in for every addon's `only` (G-14). */
  force?: boolean;
}

export type MenuDeclarations = Readonly<Record<string, MenuEntry>>;

/** Whether an addon authored its menus as the legacy declaration list rather than the dict. */
export function isMenuDeclarationList(
  menus: readonly BaseMenuItem[] | MenuDeclarations | undefined,
): menus is readonly BaseMenuItem[] {
  return Array.isArray(menus);
}

export interface MenuLayer extends Layer {
  menus?: readonly BaseMenuItem[] | MenuDeclarations;
  /** The layer's routes, which its dependents may mount. */
  routes?: readonly { name: string }[];
}

/** A logical-tree node; `flatten` marks an included app rendered into its parent. */
export interface CompiledMenuItem extends ChromeMenuItem {
  flatten?: boolean;
  children?: readonly CompiledMenuItem[];
}

/** A borrowed page: the alias route a `mount` node adds under its app. */
export interface MenuMount {
  /** The node id, which names the alias route. */
  id: string;
  /** The mounted route. */
  route: string;
  /** The alias's full path: its app's `path` joined with the node's. */
  path: string;
  /** The layer that set `mount`, which depends on the route's owner. */
  by: string;
  recordMatch?: NonNullable<AddonRoute["recordMatch"]>;
  defaultResourceView?: string;
}

export interface CompiledMenus {
  /** Every surviving node in place: owns routes, trails and the active app. */
  logical: readonly CompiledMenuItem[];
  /**
   * What the chrome shows: flattened apps lifted into their aggregator, and nodes
   * left out of the rail kept with `hidden`, so the palette and admission keep them.
   */
  navigation: readonly ChromeMenuItem[];
  /** Removed nodes, subtrees included; `parent` is the rail item each showed under. */
  removed: readonly RemovedMenuItem[];
  /** Every mount node's alias, removed ones included so their pages become unavailable. */
  mounts: readonly MenuMount[];
  /** Surviving nodes left out of the rail, by a `hide` or by a layer's `only`. */
  hidden: readonly HiddenMenuItem[];
  /** The layer that set each node field, declarations included. */
  provenance: Readonly<Record<string, Readonly<Record<string, string>>>>;
  /** Non-fatal findings, such as menu ids declared outside the addon's namespace. */
  diagnostics: readonly string[];
}

const DECLARATION_FIELDS = [
  "label", "route", "params", "defaultResourceView", "to", "icon",
  "appRoot", "description", "group", "status", "tone", "personal", "parent", "sequence", "before", "after",
  "mount", "path", "recordMatch",
] as const satisfies readonly (keyof MenuEntry)[];
const OPERATION_FIELDS = ["include", "remove", "hide", "only", "force"] as const satisfies readonly (keyof MenuEntry)[];
type EntryKey = (typeof DECLARATION_FIELDS)[number] | (typeof OPERATION_FIELDS)[number];
// Every MenuEntry key is a declaration field or an operation; adding one without listing it fails here.
const _complete: Exclude<keyof MenuEntry, EntryKey> extends never ? true : never = true;
void _complete;
const ENTRY_KEYS: ReadonlySet<string> = new Set<string>([...DECLARATION_FIELDS, ...OPERATION_FIELDS]);
/** Fields a node carries; `badge` arrives only through the legacy list. */
type Field = (typeof DECLARATION_FIELDS)[number] | "badge" | "hide" | "remove" | "flatten";
/** Resolution-only fields, never emitted on a compiled item; a mount becomes a `route` to its alias. */
const RESOLUTION_FIELDS: readonly Field[] = ["parent", "sequence", "before", "after", "hide", "remove", "mount", "path", "recordMatch"];

interface Node {
  id: string;
  owner: string;
  declaredRoot: boolean;
  index: number;
  fields: Partial<Record<Field, unknown>>;
  setBy: Partial<Record<Field, string>>;
  /** Each layer's allowlist of children, intersected at resolution. */
  only: { layer: string; ids: ReadonlySet<string>; force: boolean }[];
}

/**
 * Compile the composed addons' menus into one logical tree and its navigation projection.
 *
 * Declarations come first, in layer order; alterations then apply along
 * dependencies: a layer may alter only nodes declared by an addon it depends
 * on, a dependent overrides its dependency field by field, and two unrelated
 * layers setting one field fail. The deployment layer depends on every addon,
 * so it may alter anything, last.
 */
export function compileMenus(
  layers: readonly MenuLayer[],
  ancestors: ReadonlyMap<string, ReadonlySet<string>> = layerAncestry(layers),
): CompiledMenus {
  const nodes = new Map<string, Node>();
  const alterations: { layer: string; id: string; entry: MenuEntry }[] = [];
  const diagnostics: string[] = [];

  const declare = (layer: string, id: string, fields: Node["fields"]): void => {
    if (nodes.has(id)) throw new Error(`Addon "${layer}" redefines menu item id "${id}" already contributed by another addon.`);
    const setBy = Object.fromEntries(Object.keys(fields).map((field) => [field, layer]));
    nodes.set(id, { id, owner: layer, declaredRoot: fields.parent == null, index: nodes.size, fields, setBy, only: [] });
  };
  for (const layer of layers) {
    if (isMenuDeclarationList(layer.menus)) {
      if (layer.id === DEPLOYMENT_LAYER_ID) throw new Error("ANGEE_UI.menus must be a mapping of menu ids to alterations.");
      for (const item of layer.menus) declareLegacy(declare, diagnostics, layer.id, item, undefined);
      continue;
    }
    for (const [id, entry] of Object.entries(layer.menus ?? {})) {
      validateEntry(layer.id, id, entry);
      const owns = layer.id !== DEPLOYMENT_LAYER_ID && (id === layer.id || id.startsWith(`${layer.id}.`));
      if (owns) {
        declare(layer.id, id, pick(entry));
        if (entry.include || entry.hide !== undefined || entry.remove || entry.only) {
          alterations.push({ layer: layer.id, id, entry: { include: entry.include, hide: entry.hide, remove: entry.remove, only: entry.only } });
        }
      } else {
        alterations.push({ layer: layer.id, id, entry });
      }
    }
  }

  const target = (layer: string, id: string): Node => {
    const node = nodes.get(id);
    if (!node) throw new Error(`Addon "${layer}" alters unknown menu item "${id}".`);
    assertMayAlter(ancestors, layer, node.owner, `menu item "${id}"`);
    return node;
  };
  const set = (layer: string, id: string, field: Field, value: unknown): void => {
    const node = target(layer, id);
    if (!overridesField(ancestors, node.setBy[field], layer, `menu "${id}" ${field}`)) return;
    node.fields[field] = value;
    node.setBy[field] = layer;
  };
  // Included nodes sort after every declaration, in include-list order.
  let includeOrder = nodes.size;
  for (const { layer, id, entry } of alterations) {
    for (const [field, value] of Object.entries(pick(entry))) set(layer, id, field as Field, value);
    if (entry.hide !== undefined) set(layer, id, "hide", entry.hide);
    if (entry.remove) set(layer, id, "remove", true);
    if (entry.only) target(layer, id).only.push({ layer, ids: new Set(entry.only), force: entry.force === true });
    for (const include of entry.include ?? []) {
      const included = typeof include === "string" ? { id: include } : include;
      target(layer, id);
      set(layer, included.id, "parent", id);
      if (nodes.get(included.id)!.setBy.parent === layer) nodes.get(included.id)!.index = includeOrder++;
      if (included.flatten) set(layer, included.id, "flatten", true);
    }
  }
  const compiled = resolve(nodes, ancestors, diagnostics);
  // An addon borrows only pages of the addons it depends on, like any alteration.
  const routeOwners = new Map(layers.flatMap((layer) => (layer.routes ?? []).map((route) => [route.name, layer.id] as const)));
  for (const mount of compiled.mounts) {
    const owner = routeOwners.get(mount.route);
    if (owner === undefined) throw new Error(`Menu item "${mount.id}" mounts unknown route "${mount.route}".`);
    assertMayAlter(ancestors, mount.by, owner, `route "${mount.route}"`, "mounts");
  }
  return compiled;
}

/** Refuse unknown keys and malformed values, which would otherwise do nothing silently. */
function validateEntry(layer: string, id: string, entry: unknown): asserts entry is MenuEntry {
  const where = `Menu entry "${id}" of "${layer}"`;
  if (typeof entry !== "object" || entry === null || Array.isArray(entry)) throw new Error(`${where} must be a mapping.`);
  for (const key of Object.keys(entry)) {
    if (!ENTRY_KEYS.has(key)) throw new Error(`${where} has unknown key "${key}".`);
  }
  const value = entry as Record<string, unknown>;
  const strings = (key: string, list: unknown): void => {
    if (!Array.isArray(list) || !list.every((item) => typeof item === "string" || (key === "include"
      && typeof item === "object" && item !== null && typeof (item as { id?: unknown }).id === "string"))) {
      throw new Error(`${where}: ${key} must be a list of menu ids.`);
    }
  };
  if (value.include !== undefined) strings("include", value.include);
  if (value.only !== undefined) strings("only", value.only);
  for (const key of ["remove", "hide", "appRoot", "personal", "force"] as const) {
    if (value[key] !== undefined && typeof value[key] !== "boolean") throw new Error(`${where}: ${key} must be true or false.`);
  }
  // Addons only narrow (G-14); the deployment may force an `only`, and says so.
  if (value.force !== undefined && (layer !== DEPLOYMENT_LAYER_ID || value.only === undefined)) {
    throw new Error(`${where}: only the deployment forces, with force beside an only.`);
  }
  for (const key of ["sequence"] as const) {
    if (value[key] !== undefined && typeof value[key] !== "number") throw new Error(`${where}: ${key} must be a number.`);
  }
  for (const key of ["label", "route", "mount", "path", "to", "icon", "before", "after"] as const) {
    if (value[key] !== undefined && typeof value[key] !== "string") throw new Error(`${where}: ${key} must be a string.`);
  }
  const match = value.recordMatch as { field?: unknown; equals?: unknown } | null | undefined;
  if (match !== undefined && (typeof match !== "object" || match === null
    || typeof match.field !== "string" || typeof match.equals !== "string")) {
    throw new Error(`${where}: recordMatch must be { field, equals } strings.`);
  }
  if (value.parent !== undefined && value.parent !== null && typeof value.parent !== "string") {
    throw new Error(`${where}: parent must be a menu id or null.`);
  }
}

function declareLegacy(
  declare: (layer: string, id: string, fields: Node["fields"]) => void,
  diagnostics: string[],
  layer: string,
  item: BaseMenuItem,
  parent: string | undefined,
): void {
  if ("app" in item) throw new Error(`Menu item "${item.id}" authors app; app identity is compiler-emitted.`);
  const id = item.id ?? item.route;
  if (!id) throw new Error(`Addon "${layer}" declares a menu item without id or route; menu id defaults require one of them.`);
  const { id: _id, children, parentId, ...rest } = item;
  const owner = parent ?? parentId;
  if (id !== layer && !id.startsWith(`${layer}.`)) {
    diagnostics.push(`Addon "${layer}" declares menu item "${id}" outside its namespace ("${layer}" or "${layer}.…").`);
  }
  declare(layer, id, { ...rest, ...(owner ? { parent: owner } : {}) });
  for (const child of children ?? []) declareLegacy(declare, diagnostics, layer, child, id);
}

function pick(entry: MenuEntry): Node["fields"] {
  const fields: Node["fields"] = {};
  for (const field of DECLARATION_FIELDS) if (entry[field] !== undefined) fields[field] = entry[field];
  return fields;
}

function resolve(
  nodes: Map<string, Node>,
  ancestors: ReadonlyMap<string, ReadonlySet<string>>,
  diagnostics: readonly string[],
): CompiledMenus {
  const parentOf = (node: Node): string | undefined => {
    const parent = node.fields.parent as string | null | undefined;
    if (parent === undefined || parent === null) return undefined;
    if (!nodes.has(parent)) throw new Error(`Menu item "${node.id}" names unknown parent "${parent}".`);
    return parent;
  };
  const children = new Map<string | undefined, Node[]>();
  for (const node of nodes.values()) {
    const parent = parentOf(node);
    children.set(parent, [...(children.get(parent) ?? []), node]);
    for (const anchor of [node.fields.before, node.fields.after]) {
      if (anchor !== undefined && !nodes.has(anchor as string)) {
        throw new Error(`Menu item "${node.id}" positions itself against unknown menu item "${String(anchor)}".`);
      }
    }
    if (node.fields.before !== undefined && node.fields.after !== undefined) {
      throw new Error(`Menu item "${node.id}" sets both before and after.`);
    }
    if (node.fields.personal && (parent !== undefined || node.fields.group !== "platform")) {
      throw new Error(`Menu item "${node.id}" is personal, which only a Settings root (group "platform", no parent) may be.`);
    }
    const targets = (["route", "mount", "to"] as const).filter((field) => node.fields[field] !== undefined);
    if (targets.length > 1) {
      throw new Error(`Menu item "${node.id}" declares both ${targets[0]} and ${targets[1]}; a node targets one of route, mount and to.`);
    }
    if (node.fields.mount === undefined) {
      if (node.fields.recordMatch !== undefined) throw new Error(`Menu item "${node.id}" sets recordMatch, which only a mount claims.`);
      if (node.fields.path !== undefined && !node.declaredRoot) {
        throw new Error(`Menu item "${node.id}" sets path, which only an app or a mount has.`);
      }
    } else if (node.fields.path === undefined || node.fields.params !== undefined) {
      throw new Error(`Menu item "${node.id}" mounts "${String(node.fields.mount)}" with ${node.fields.path === undefined ? "no path" : "params"}; a mount takes a path and no params.`);
    }
  }
  for (const node of nodes.values()) {
    const seen = new Set<string>();
    for (let current: string | undefined = node.id; current; current = parentOf(nodes.get(current)!)) {
      if (seen.has(current)) throw new Error(`Menu item "${current}" creates a parent cycle.`);
      seen.add(current);
    }
  }

  // A mount lives under its app: the nearest node declared at the top of the rail,
  // which keeps its path when another addon includes it.
  const appOf = (node: Node): Node | undefined => {
    const parent = parentOf(node);
    if (parent === undefined) return undefined;
    const candidate = nodes.get(parent)!;
    return candidate.declaredRoot ? candidate : appOf(candidate);
  };
  const mounts = [...nodes.values()].filter((node) => node.fields.mount !== undefined).map((node): MenuMount => {
    const app = appOf(node);
    if (!app) throw new Error(`Menu item "${node.id}" mounts "${String(node.fields.mount)}" outside an app; mount it under one.`);
    const base = normalizeRoutePath(typeof app.fields.path === "string" ? app.fields.path : `/${app.id}`);
    const segment = (node.fields.path as string).replace(/^\/+/, "");
    return {
      id: node.id,
      route: node.fields.mount as string,
      path: normalizeRoutePath(base === "/" ? `/${segment}` : `${base}/${segment}`),
      by: node.setBy.mount!,
      ...(node.fields.recordMatch ? { recordMatch: node.fields.recordMatch as NonNullable<MenuMount["recordMatch"]> } : {}),
      ...(typeof node.fields.defaultResourceView === "string" ? { defaultResourceView: node.fields.defaultResourceView } : {}),
    };
  });

  // A flattened app renders no item of its own: its children show under its
  // nearest unflattened ancestor, as `navigationChildren` lifts them.
  const shownUnder = (node: Node): string | undefined => {
    let parent = parentOf(node);
    while (parent !== undefined && nodes.get(parent)!.fields.flatten && parentOf(nodes.get(parent)!) !== undefined) {
      parent = parentOf(nodes.get(parent)!);
    }
    return parent;
  };
  const removed = new Map<string, CompiledMenus["removed"][number]>();
  const collectRemoved = (node: Node, by: string): void => {
    if (removed.has(node.id)) return;
    const parent = shownUnder(node);
    // A removed mount takes its alias with it.
    const route = node.fields.mount !== undefined ? node.id : node.fields.route;
    removed.set(node.id, {
      id: node.id,
      ...(typeof route === "string" ? { route } : {}),
      by,
      ...(parent !== undefined ? { parent } : {}),
      ...(typeof node.fields.label === "string" ? { label: node.fields.label } : {}),
      ...(node.declaredRoot && parentOf(node) !== undefined && !node.fields.flatten ? { app: true } : {}),
    });
    for (const child of children.get(node.id) ?? []) collectRemoved(child, by);
  };
  for (const node of nodes.values()) if (node.fields.remove) collectRemoved(node, node.setBy.remove!);
  const survivors = (parent: string | undefined): Node[] =>
    (children.get(parent) ?? []).filter((node) => !removed.has(node.id));

  // Included apps lose appRoot, but retain compiler-owned app identity unless
  // flattened. An author nesting appRoot itself still fails tree validation.
  const emitted = (node: Node, drop: readonly Field[]): Record<string, unknown> => {
    const fields: Record<string, unknown> = { ...node.fields };
    for (const field of drop) delete fields[field];
    if (parentOf(node) !== undefined && node.setBy.parent !== node.owner) delete fields.appRoot;
    if (node.declaredRoot && parentOf(node) !== undefined && !node.fields.flatten) fields.app = true;
    // A mount links to its alias route, which carries its default view.
    if (node.fields.mount !== undefined) {
      fields.route = node.id;
      delete fields.defaultResourceView;
    }
    return fields;
  };
  const logicalItem = (node: Node): CompiledMenuItem => {
    const nested = position(survivors(node.id)).map(logicalItem);
    return { ...(emitted(node, RESOLUTION_FIELDS) as Omit<CompiledMenuItem, "id">), id: node.id, ...(nested.length ? { children: nested } : {}) };
  };

  // A node's navigation children: its children, with each flattened app's own
  // navigation children lifted in its place, then positioned together. The rail
  // leaves out a child that is hidden, excluded by a layer's `only` (which admits
  // listed ids, items of a listed flattened app, and items declared by the `only`
  // author or an addon depending on it), or lifted from a hidden app.
  const hidden = new Map<string, CompiledMenus["hidden"][number]>();
  const navigationChildren = (node: Node): { child: Node; hidden: boolean }[] => {
    const entries = survivors(node.id).flatMap((child) => child.fields.flatten
      ? navigationChildren(child).map((lifted) => ({ ...lifted, app: child, hidden: lifted.hidden || child.fields.hide === true }))
      : [{ child, app: undefined as Node | undefined, hidden: child.fields.hide === true }]);
    const byId = new Map(entries.map((entry) => [entry.child.id, entry]));
    // Each layer's `only` narrows; a forced deployment `only` stands in for all of them (G-14).
    const forced = node.only.filter(({ force }) => force);
    const narrowing = forced.length ? forced : node.only;
    return position(entries.map((entry) => entry.child)).map((child) => {
      const entry = byId.get(child.id)!;
      if (child.fields.hide === true) hidden.set(child.id, { id: child.id, by: child.setBy.hide!, reason: "hide" });
      const excluding = narrowing.find(({ layer, ids }) => !(ids.has(child.id) || (entry.app !== undefined && ids.has(entry.app.id))
        || child.owner === layer || Boolean(ancestors.get(child.owner)?.has(layer))));
      if (excluding && !entry.hidden) hidden.set(child.id, { id: child.id, by: excluding.layer, reason: "only" });
      return { child, hidden: entry.hidden || excluding !== undefined };
    });
  };
  const navigationItem = (node: Node, isHidden: boolean): ChromeMenuItem => {
    const nested = navigationChildren(node).map((entry) => navigationItem(entry.child, entry.hidden));
    return {
      ...(emitted(node, [...RESOLUTION_FIELDS, "flatten"]) as Omit<ChromeMenuItem, "id">),
      id: node.id,
      ...(isHidden ? { hidden: true } : {}),
      ...(nested.length ? { children: nested } : {}),
    };
  };

  const roots = position(survivors(undefined));
  for (const root of roots) if (root.fields.hide === true) hidden.set(root.id, { id: root.id, by: root.setBy.hide!, reason: "hide" });
  const navigation = roots.map((root) => navigationItem(root, root.fields.hide === true));
  return {
    logical: roots.map(logicalItem),
    navigation,
    removed: [...removed.values()],
    mounts,
    hidden: [...hidden.values()],
    provenance: Object.fromEntries([...nodes.values()].map((node) => [node.id, { ...node.setBy }])) as CompiledMenus["provenance"],
    diagnostics,
  };
}

/** Order one list of siblings by the shared rule, from declaration or include order. */
function position(siblings: readonly Node[]): Node[] {
  const entries = [...siblings].sort((left, right) => left.index - right.index).map((node) => ({
    id: node.id,
    node,
    ...(typeof node.fields.sequence === "number" ? { sequence: node.fields.sequence } : {}),
    ...(typeof node.fields.before === "string" ? { before: node.fields.before } : {}),
    ...(typeof node.fields.after === "string" ? { after: node.fields.after } : {}),
  }));
  return positionSiblings(entries, "Menu items").map((entry) => entry.node);
}

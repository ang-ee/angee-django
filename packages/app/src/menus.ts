import type { BaseMenuItem, ChromeMenuExtra, ChromeMenuItem } from "@angee/ui/chrome/menu-tree";
import type { MenuItem } from "@angee/ui/runtime";

import { layerAncestry, type Layer } from "./layers";
import { DEPLOYMENT_LAYER_ID } from "./shell";

/** An included node, optionally rendered flat into the including node. */
export type MenuInclude = string | { id: string; flatten?: boolean };

/**
 * One key of an addon's `menus` dict. A key in the addon's own namespace (its
 * id, or `<id>.…`) declares a node; any other key alters a node declared by an
 * addon this one depends on.
 */
export interface MenuEntry extends Omit<MenuItem, "id" | "children">, Omit<ChromeMenuExtra, "parentId" | "badge"> {
  /** The parent node id; `null` places the node at the top of the rail. */
  parent?: string | null;
  sequence?: number;
  before?: string;
  after?: string;
  /** Nodes that become children of this one, with their subtrees. */
  include?: readonly MenuInclude[];
  /** Structural removal: the node and its subtree leave, and pages only they reach become unavailable. */
  remove?: boolean;
  /** Cosmetic: the node leaves the navigation but keeps its pages reachable; `false` shows it again. */
  hide?: boolean;
  /** Allowlist of visible children; layers narrow it, never widen it. */
  only?: readonly string[];
}

export type MenuDeclarations = Readonly<Record<string, MenuEntry>>;

export interface MenuLayer extends Layer {
  menus?: readonly BaseMenuItem[] | MenuDeclarations;
}

/** A logical-tree node; `flatten` marks an included app rendered into its parent. */
export interface CompiledMenuItem extends ChromeMenuItem {
  flatten?: boolean;
  children?: readonly CompiledMenuItem[];
}

export interface CompiledMenus {
  /** Every surviving node in place: owns routes, trails and the active app. */
  logical: readonly CompiledMenuItem[];
  /** What the rail, Settings and palette show: hidden nodes dropped, flattened apps lifted. */
  navigation: readonly ChromeMenuItem[];
  /** Removed nodes, subtrees included, with the routes they referenced. */
  removed: readonly { id: string; route?: string }[];
  /** The layer that set each altered node field. */
  provenance: Readonly<Record<string, Readonly<Record<string, string>>>>;
}

const DECLARATION_FIELDS = [
  "label", "route", "params", "defaultResourceView", "to", "icon",
  "appRoot", "description", "group", "status", "tone", "parent", "sequence", "before", "after",
] as const satisfies readonly (keyof MenuEntry)[];
type Field = (typeof DECLARATION_FIELDS)[number] | "hide" | "remove" | "flatten";

interface Node {
  id: string;
  owner: string;
  index: number;
  fields: Partial<Record<Field, unknown>>;
  setBy: Partial<Record<Field, string>>;
  /** Each layer's allowlist of children, intersected at resolution. */
  only: { layer: string; ids: ReadonlySet<string> }[];
}

/**
 * Compile the composed addons' menus into one logical tree and its navigation projection.
 *
 * Declarations come first, in layer order; alterations then apply along
 * dependencies: a layer may alter only nodes declared by an addon it depends
 * on, a dependent overrides its dependency field by field, two unrelated layers
 * setting one field fail, and the deployment layer may alter anything last.
 */
export function compileMenus(layers: readonly MenuLayer[]): CompiledMenus {
  const ancestors = layerAncestry(layers);
  const nodes = new Map<string, Node>();
  const alterations: { layer: string; id: string; entry: MenuEntry }[] = [];

  const declare = (layer: string, id: string, fields: Node["fields"]): void => {
    if (nodes.has(id)) throw new Error(`Addon "${layer}" redefines menu item id "${id}" already contributed by another addon.`);
    const setBy = Object.fromEntries(Object.keys(fields).map((field) => [field, layer]));
    nodes.set(id, { id, owner: layer, index: nodes.size, fields, setBy, only: [] });
  };
  for (const layer of layers) {
    if (!layer.menus) continue;
    if (Array.isArray(layer.menus)) {
      for (const item of layer.menus as readonly BaseMenuItem[]) declareLegacy(declare, layer.id, item, undefined);
      continue;
    }
    for (const [id, entry] of Object.entries(layer.menus as MenuDeclarations)) {
      const owns = id === layer.id || id.startsWith(`${layer.id}.`);
      if (owns && layer.id !== DEPLOYMENT_LAYER_ID) {
        declare(layer.id, id, pick(entry));
        if (entry.include || entry.hide !== undefined || entry.remove || entry.only) {
          alterations.push({ layer: layer.id, id, entry: { include: entry.include, hide: entry.hide, remove: entry.remove, only: entry.only } });
        }
      } else {
        alterations.push({ layer: layer.id, id, entry });
      }
    }
  }

  const provenance: Record<string, Record<string, string>> = {};
  const set = (layer: string, id: string, field: Field, value: unknown): void => {
    const node = target(layer, id);
    const previous = node.setBy[field];
    if (previous !== undefined && previous !== layer && layer !== DEPLOYMENT_LAYER_ID && !ancestors.get(layer)?.has(previous)) {
      if (ancestors.get(previous)?.has(layer)) return;
      throw new Error(`Unrelated addons "${previous}" and "${layer}" both set menu "${id}" ${field}.`);
    }
    node.fields[field] = value;
    node.setBy[field] = layer;
    (provenance[id] ??= {})[field] = layer;
  };
  const target = (layer: string, id: string): Node => {
    const node = nodes.get(id);
    if (!node) throw new Error(`Addon "${layer}" alters unknown menu item "${id}".`);
    if (layer !== DEPLOYMENT_LAYER_ID && node.owner !== layer && !ancestors.get(layer)?.has(node.owner)) {
      throw new Error(`Addon "${layer}" alters menu item "${id}" of "${node.owner}", which it does not depend on.`);
    }
    return node;
  };
  // Included nodes sort after every declaration, in include-list order.
  let includeOrder = nodes.size;
  for (const { layer, id, entry } of alterations) {
    for (const [field, value] of Object.entries(pick(entry))) set(layer, id, field as Field, value);
    if (entry.hide !== undefined) set(layer, id, "hide", entry.hide);
    if (entry.remove) set(layer, id, "remove", true);
    if (entry.only) target(layer, id).only.push({ layer, ids: new Set(entry.only) });
    for (const include of entry.include ?? []) {
      const included = typeof include === "string" ? { id: include } : include;
      target(layer, id);
      set(layer, included.id, "parent", id);
      if (nodes.get(included.id)!.setBy.parent === layer) nodes.get(included.id)!.index = includeOrder++;
      if (included.flatten) set(layer, included.id, "flatten", true);
    }
  }
  return resolve(nodes, ancestors, provenance);
}

function declareLegacy(
  declare: (layer: string, id: string, fields: Node["fields"]) => void,
  layer: string,
  item: BaseMenuItem,
  parent: string | undefined,
): void {
  const id = item.id ?? item.route;
  if (!id) throw new Error(`Addon "${layer}" declares a menu item without id or route; menu id defaults require one of them.`);
  const { id: _id, children, parentId, badge: _badge, ...rest } = item;
  const owner = parent ?? parentId;
  declare(layer, id, { ...rest, ...(owner ? { parent: owner } : {}) });
  for (const child of children ?? []) declareLegacy(declare, layer, child, id);
}

function pick(entry: MenuEntry): Node["fields"] {
  const fields: Node["fields"] = {};
  for (const field of DECLARATION_FIELDS) if (entry[field] !== undefined) fields[field] = entry[field];
  return fields;
}

function resolve(
  nodes: Map<string, Node>,
  ancestors: ReadonlyMap<string, ReadonlySet<string>>,
  provenance: Record<string, Record<string, string>>,
): CompiledMenus {
  const parentOf = (node: Node): string | undefined => {
    const parent = node.fields.parent as string | null | undefined;
    if (parent === undefined || parent === null || parent === "shell#rail") return undefined;
    if (!nodes.has(parent)) throw new Error(`Menu item "${node.id}" names unknown parent "${parent}".`);
    return parent;
  };
  const children = new Map<string | undefined, Node[]>();
  for (const node of nodes.values()) {
    const parent = parentOf(node);
    children.set(parent, [...(children.get(parent) ?? []), node]);
  }
  for (const node of nodes.values()) {
    const seen = new Set<string>();
    for (let current: string | undefined = node.id; current; current = parentOf(nodes.get(current)!)) {
      if (seen.has(current)) throw new Error(`Menu item "${current}" creates a parent cycle.`);
      seen.add(current);
    }
  }

  const removed: CompiledMenus["removed"][number][] = [];
  const collectRemoved = (node: Node): void => {
    removed.push({ id: node.id, ...(typeof node.fields.route === "string" ? { route: node.fields.route } : {}) });
    for (const child of children.get(node.id) ?? []) collectRemoved(child);
  };
  for (const node of nodes.values()) if (node.fields.remove && !removed.some((entry) => entry.id === node.id)) collectRemoved(node);
  const gone = new Set(removed.map((entry) => entry.id));

  const ordered = (parent: string | undefined): Node[] => {
    const siblings = (children.get(parent) ?? []).filter((node) => !gone.has(node.id));
    siblings.sort((left, right) => sequenceOf(left) - sequenceOf(right) || left.index - right.index);
    const constrained = siblings.filter((node) => node.fields.before !== undefined || node.fields.after !== undefined);
    for (const node of constrained) {
      const anchor = (node.fields.before ?? node.fields.after) as string;
      if (node.fields.before !== undefined && node.fields.after !== undefined) {
        throw new Error(`Menu item "${node.id}" sets both before and after.`);
      }
      if (gone.has(anchor)) continue;
      const at = siblings.findIndex((sibling) => sibling.id === anchor);
      if (at < 0) throw new Error(`Menu item "${node.id}" positions itself against "${anchor}", which is not a sibling.`);
      siblings.splice(siblings.indexOf(node), 1);
      const position = siblings.findIndex((sibling) => sibling.id === anchor);
      siblings.splice(node.fields.before !== undefined ? position : position + 1, 0, node);
    }
    for (const node of constrained) {
      const anchor = (node.fields.before ?? node.fields.after) as string;
      if (gone.has(anchor)) continue;
      const delta = siblings.indexOf(node) - siblings.findIndex((sibling) => sibling.id === anchor);
      if (node.fields.before !== undefined ? delta !== -1 : delta !== 1) {
        throw new Error(`Menu item "${node.id}" has conflicting before/after constraints among its siblings.`);
      }
    }
    return siblings;
  };

  // Only a root is an app: an app another layer included keeps its place, not the
  // marker. An author nesting appRoot itself still fails tree validation.
  const included = (node: Node): boolean => parentOf(node) !== undefined && node.setBy.parent !== node.owner;
  const logicalItem = (node: Node): CompiledMenuItem => {
    const { parent: _parent, sequence: _sequence, before: _before, after: _after, hide: _hide, remove: _remove, ...fields } = node.fields;
    if (included(node)) delete fields.appRoot;
    const nested = ordered(node.id).map(logicalItem);
    return { ...(fields as Omit<CompiledMenuItem, "id">), id: node.id, ...(nested.length ? { children: nested } : {}) };
  };
  // A node's navigation children: its visible children, a flattened app's visible
  // children lifted in its place, all ordered by sequence (stable otherwise). Each
  // layer's `only` admits listed ids, items of a listed flattened app, and items
  // declared by the `only` author or an addon depending on it.
  const navigationChildren = (node: Node): { child: Node; app?: Node }[] => {
    const shown = (child: Node): boolean => child.fields.hide !== true;
    const lifted: { child: Node; app?: Node }[] = ordered(node.id).filter(shown).flatMap((child) => child.fields.flatten
      ? ordered(child.id).filter(shown).map((grandchild) => ({ child: grandchild, app: child }))
      : [{ child }]);
    lifted.sort((left, right) => sequenceOf(left.child) - sequenceOf(right.child) || 0);
    return lifted.filter(({ child, app }) => node.only.every(({ layer, ids }) =>
      ids.has(child.id) || (app !== undefined && ids.has(app.id))
      || child.owner === layer || Boolean(ancestors.get(child.owner)?.has(layer))));
  };
  const navigationItem = (node: Node): ChromeMenuItem => {
    const { parent: _parent, sequence: _sequence, before: _before, after: _after, hide: _hide, remove: _remove, flatten: _flatten, ...fields } = node.fields;
    if (included(node)) delete fields.appRoot;
    const nested = navigationChildren(node).map(({ child }) => navigationItem(child));
    return { ...(fields as Omit<ChromeMenuItem, "id">), id: node.id, ...(nested.length ? { children: nested } : {}) };
  };
  const roots = ordered(undefined);
  return {
    logical: roots.map(logicalItem),
    navigation: roots.filter((node) => node.fields.hide !== true).map(navigationItem),
    removed,
    provenance,
  };
}

function sequenceOf(node: Node): number {
  return typeof node.fields.sequence === "number" ? node.fields.sequence : Number.POSITIVE_INFINITY;
}

// Addon composition. Each addon describes itself once with `defineAddon`; an
// app folds the manifests into a single runtime with `composeAddons`. Registry
// facts (routes, widgets, icons, i18n keys) must be contributed by exactly one
// addon — a collision is a build-time error. Ordered contribution lists
// (chatter tabs, slot entries) dedupe by key, last wins, and sort by sequence.

import type { I18nResources } from "./i18n";

/** A route an addon contributes; the chrome is the shell named by `shell`. */
export interface AddonRoute {
  /** Stable, addon-namespaced name (e.g. `notes.detail`); unique across addons. */
  name: string;
  /** Router path pattern (e.g. `/notes`, `/notes/$id`). */
  path: string;
  /** Optional route name this route nests under in the rendered route tree. */
  parent?: string;
  /** Which shell renders this route's chrome (`console`, `public`, …). */
  shell: string;
}

/** A navigation entry; many menu items may target one route. */
export interface MenuItem {
  /** Stable menu id. Defaults to `route` when omitted. */
  id?: string;
  label?: string;
  children?: readonly MenuItem[];
  /**
   * Route name this item targets. The rendered binding resolves `to` from the
   * route path and may derive route chrome from the item's root ancestor:
   * root title/icon, linked ancestor crumbs, and a plain leaf crumb.
   */
  route?: string;
  to?: string;
  icon?: string;
}

/** A composed navigation entry with defaults applied. */
export interface ComposedMenuItem extends Omit<MenuItem, "children" | "id"> {
  id: string;
  children?: readonly ComposedMenuItem[];
}

/** Field-widget registry: widget id -> renderer (opaque to the headless SDK). */
export type WidgetMap = Record<string, unknown>;

/**
 * Per-model create-form override: model name -> a declarative create form (the
 * rendered binding interprets it). An addon registers the form for a model it
 * owns; the standard form renderer uses it wherever that model is created,
 * including the relation-picker inline create. Opaque to the headless SDK.
 */
export type FormOverrideMap = Record<string, unknown>;

/** A chatter aside tab; merges by `id` (last wins) and orders by `sequence`. */
export interface ChatterContribution {
  id: string;
  sequence?: number;
}

/** A contribution into a UI slot another addon owns; merges by `(slot, id)`. */
export interface SlotContribution {
  slot: string;
  id: string;
  sequence?: number;
  content?: unknown;
}

/**
 * A file-preview renderer contributed at build time; merges by `id` (fail-fast
 * on collision, like widgets). The headless SDK only needs the `id` to detect
 * collisions — the rendered binding owns the mime matcher, component, and
 * priority and reads the rest as its own `PreviewProvider`.
 */
export interface PreviewContribution {
  id: string;
}

/** One addon's self-describing manifest. */
export interface AddonManifest {
  id: string;
  routes?: readonly AddonRoute[];
  menus?: readonly MenuItem[];
  widgets?: WidgetMap;
  i18n?: I18nResources;
  icons?: Readonly<Record<string, unknown>>;
  forms?: FormOverrideMap;
  chatter?: readonly ChatterContribution[];
  slots?: readonly SlotContribution[];
  previews?: readonly PreviewContribution[];
}

/** The merged runtime an app composes from its addon manifests. */
export interface ComposedAddons {
  routes: readonly AddonRoute[];
  menus: readonly ComposedMenuItem[];
  widgets: WidgetMap;
  i18n: I18nResources;
  icons: Readonly<Record<string, unknown>>;
  forms: FormOverrideMap;
  chatter: readonly ChatterContribution[];
  slots: readonly SlotContribution[];
  previews: readonly PreviewContribution[];
}

/** Brand an object as an addon manifest, giving one greppable declaration site. */
export function defineAddon(manifest: AddonManifest): AddonManifest {
  return manifest;
}

/**
 * Merge sequence-ordered contributions: dedupe by `keyOf` (later groups win, so
 * an addon overrides a default) and sort by `sequence`. The chatter and slot
 * merges are the same fold over different keys.
 */
function mergeByKey<T extends { sequence?: number }>(
  groups: readonly (readonly T[])[],
  keyOf: (item: T) => string,
): T[] {
  const byKey = new Map<string, T>();
  for (const group of groups) {
    for (const item of group) byKey.set(keyOf(item), item);
  }
  return [...byKey.values()].sort(
    (a, b) => (a.sequence ?? 0) - (b.sequence ?? 0),
  );
}

export function mergeChatterContributions(
  ...groups: readonly (readonly ChatterContribution[])[]
): ChatterContribution[] {
  return mergeByKey(groups, (tab) => tab.id);
}

export function mergeSlotContributions(
  ...groups: readonly (readonly SlotContribution[])[]
): SlotContribution[] {
  return mergeByKey(groups, (entry) => `${entry.slot}\0${entry.id}`);
}

/** Claim a registry key for one addon, failing fast on a second claim. */
function claim(
  registry: Record<string, unknown>,
  key: string,
  addonId: string,
  kind: string,
): void {
  if (Object.prototype.hasOwnProperty.call(registry, key)) {
    throw new Error(
      `Addon "${addonId}" redefines ${kind} "${key}" already contributed by another addon.`,
    );
  }
}

/**
 * Fold addon manifests into one runtime. Registry facts must be unique; ordered
 * contribution lists are merged by key and sorted by sequence.
 */
export function composeAddons(addons: readonly AddonManifest[]): ComposedAddons {
  const routes: AddonRoute[] = [];
  const menus: ComposedMenuItem[] = [];
  const widgets: WidgetMap = {};
  const i18n: Record<string, Record<string, string>> = {};
  const icons: Record<string, unknown> = {};
  const forms: Record<string, unknown> = {};
  const previews: PreviewContribution[] = [];
  const routeNames: Record<string, true> = {};
  const menuIds: Record<string, true> = {};
  const previewIds: Record<string, true> = {};

  for (const addon of addons) {
    if (addon.routes) {
      for (const route of addon.routes) {
        claim(routeNames, route.name, addon.id, "route name");
        routeNames[route.name] = true;
        routes.push(route);
      }
    }
    if (addon.menus) {
      menus.push(...normalizeMenuItems(menuIds, addon.menus, addon.id));
    }
    if (addon.widgets) {
      for (const [key, widget] of Object.entries(addon.widgets)) {
        claim(widgets, key, addon.id, "widget");
        widgets[key] = widget;
      }
    }
    if (addon.icons) {
      for (const [name, icon] of Object.entries(addon.icons)) {
        claim(icons, name, addon.id, "icon");
        icons[name] = icon;
      }
    }
    if (addon.forms) {
      for (const [model, form] of Object.entries(addon.forms)) {
        claim(forms, model, addon.id, "form override");
        forms[model] = form;
      }
    }
    if (addon.i18n) {
      for (const [namespace, messages] of Object.entries(addon.i18n)) {
        const target = (i18n[namespace] ??= {});
        for (const [key, value] of Object.entries(messages)) {
          claim(target, key, addon.id, `i18n key "${namespace}.${key}"`);
          target[key] = value;
        }
      }
    }
    if (addon.previews) {
      for (const preview of addon.previews) {
        claim(previewIds, preview.id, addon.id, "preview");
        previews.push(preview);
      }
    }
  }

  return {
    routes,
    menus,
    widgets,
    i18n,
    icons,
    forms,
    chatter: mergeChatterContributions(...addons.map((a) => a.chatter ?? [])),
    slots: mergeSlotContributions(...addons.map((a) => a.slots ?? [])),
    previews,
  };
}

function normalizeMenuItems(
  registry: Record<string, unknown>,
  items: readonly MenuItem[],
  addonId: string,
): ComposedMenuItem[] {
  return items.map((item) => normalizeMenuItem(registry, item, addonId));
}

function normalizeMenuItem(
  registry: Record<string, unknown>,
  item: MenuItem,
  addonId: string,
): ComposedMenuItem {
  const id = menuItemId(item, addonId);
  claim(registry, id, addonId, "menu item id");
  registry[id] = true;
  const { id: _id, children, ...rest } = item;
  return {
    ...rest,
    id,
    ...(children
      ? { children: normalizeMenuItems(registry, children, addonId) }
      : {}),
  };
}

function menuItemId(item: MenuItem, addonId: string): string {
  if (item.id) return item.id;
  if (item.route) return item.route;
  throw new Error(
    `Addon "${addonId}" declares a menu item without id or route; menu id defaults require one of them.`,
  );
}

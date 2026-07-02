// Runtime contribution contracts. The type-only shapes the rendered binding's
// runtime registry (`runtime.ts`) and its render surfaces (menus, slots,
// previews, widgets, forms) consume. The binding OWNS these contracts; the
// addon-composition functions (`defineAddon` / `composeAddons`) in `@angee/app`
// build manifests against them.

import type { ReactNode } from "react";

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
 * Per-resource create-form override: resource id -> a declarative create form
 * (the rendered binding interprets it). An addon registers the form for a
 * resource it owns; the standard form renderer uses it wherever that resource is
 * created, including the relation-picker inline create. Opaque to the headless SDK.
 */
export type FormOverrideMap = Record<string, unknown>;

/** The generic view envelope passed to cross-page chatter surfaces. */
export interface ChatterView {
  kind: "dashboard" | "list" | "record";
  type: string;
  sqid?: string;
  sqids?: string[];
  params?: Record<string, unknown>;
}

/** A composed route's contribution to the active chatter view. */
export interface ChatterRoute {
  name: string;
  path: string;
  viewType: string;
  modelLabel?: string;
  recordParam?: string;
}

/** Runtime context for rendering a chatter tab on the active page. */
export interface ChatterViewContext {
  pathname: string;
  params: Readonly<Record<string, string>>;
  route?: ChatterRoute;
  view: ChatterView;
}

/** A chatter aside tab; merges by `id` (last wins) and orders by `sequence`. */
export interface ChatterContribution {
  id: string;
  sequence?: number;
  label?: ReactNode;
  icon?: string;
  count?: number;
  panelClassName?: string;
  render?: (context: ChatterViewContext) => ReactNode;
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

/** A drawer edge the console shell anchors a non-modal overlay to. */
export type DrawerEdge = "right" | "bottom";

/**
 * A non-modal overlay drawer contributed at build time. Pulled out by the shell's
 * edge stripe-tabs, sticky across navigation (mounted once above the router
 * outlet), and tabbed (multiple drawers on one edge become sibling tabs). Merges
 * by `(edge, id)` (fail-fast on collision, like widgets/previews) and orders by
 * `sequence`. The shell renders `render()` into a plain edge-anchored panel —
 * no scrim, no focus trap (JetBrains "Undock").
 */
export interface DrawerContribution {
  id: string;
  edge: DrawerEdge;
  title: string;
  icon?: string;
  sequence?: number;
  render: () => ReactNode;
}

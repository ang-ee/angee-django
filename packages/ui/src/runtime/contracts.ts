// Runtime contribution contracts. The type-only shapes the rendered binding's
// runtime registry (`runtime.ts`) and its render surfaces (menus, slots,
// previews, widgets, forms) consume. The binding OWNS these contracts; the
// addon-composition functions (`defineAddon` / `composeAddons`) in `@angee/app`
// build manifests against them.

import type { ReactElement, ReactNode } from "react";
import type { I18nResources } from "@angee/refine";
import type { ResourceVocabulary } from "@angee/metadata";

import type { RouteHrefParams } from "./route-href";

/** Product identity declared once by an addon; mark names a registered glyph. */
export interface RuntimeBrand {
  name: string;
  mark: string;
}

/** A menu item a layer removed: the route it referenced, the removing layer, the rail item it showed under, its label. */
export interface RemovedMenuItem {
  id: string;
  route?: string;
  by: string;
  parent?: string;
  label?: string;
}

/** A surviving menu item left out of the rail, by a `hide` or by a layer's `only`. */
export interface HiddenMenuItem {
  id: string;
  by: string;
  reason: "hide" | "only";
}

/**
 * How the composition came out, layer by layer: what developer mode shows. It
 * holds composition facts only (ids, addon names, route names), never records.
 */
export interface RuntimeComposition {
  shell: {
    home?: string;
    brand: RuntimeBrand | null;
    perspective: { id: string; root: string; home?: string } | null;
    /** The layer that supplied each resolved shell field. */
    provenance: Readonly<Partial<Record<string, string>>>;
    diagnostics: readonly string[];
  };
  /** What the app runs with once deprecated `createApp` inputs override the shell. */
  effective: { home: string; confineTo: string | null };
  menus: {
    /** The layer that set each menu item field, declarations included. */
    provenance: Readonly<Record<string, Readonly<Record<string, string>>>>;
    removed: readonly RemovedMenuItem[];
    hidden: readonly HiddenMenuItem[];
    /** Console routes a removal made unavailable, with the reason. */
    unavailable: Readonly<Record<string, string>>;
    diagnostics: readonly string[];
  };
  containers?: {
    /** Children a layer removed from a container. */
    removed: readonly { address: string; id: string; by: string }[];
    /** Each layer's narrowing per address: kept and dropped children, hides, and the condition. */
    rules: readonly { address: string; layer: string; summary: string }[];
    /** The layer behind each child field, keyed `address/id`. */
    provenance: Readonly<Record<string, Readonly<Record<string, string>>>>;
    diagnostics: readonly string[];
  };
}

/** Scoped presentation only; unknown message, resource, field and menu keys fail at boot. */
export interface AppVocabulary {
  /** Menu root owning this vocabulary. */
  app: string;
  /** Optional collection/page route; record children inherit it. */
  route?: string;
  messages?: I18nResources;
  resources?: Readonly<Record<string, ResourceVocabulary>>;
  menus?: Readonly<Record<string, string>>;
}

export interface RuntimeVocabulary {
  resources: Readonly<Record<string, ResourceVocabulary>>;
  menus: Readonly<Record<string, string>>;
}

/** A navigation entry; many menu items may target one route. */
export interface MenuItem {
  /** Stable menu id. Defaults to `route` when omitted. */
  id?: string;
  label?: string;
  children?: readonly MenuItem[];
  /**
   * Route name this item targets. The rendered binding resolves its href from
   * the route and params and may derive route chrome from the item's root
   * ancestor: root title/icon, linked ancestor crumbs, and a plain leaf crumb.
   */
  route?: string;
  /** Route parameters for a parameterized `route`. */
  params?: RouteHrefParams;
  /** Named shipped view selected when following this menu entry. */
  defaultResourceView?: string;
  /** External URL. Internal app destinations use `route` and optional `params`. */
  to?: string;
  icon?: string;
}

/** A composed navigation entry with defaults and its runtime target applied. */
export interface ComposedMenuItem
  extends Omit<MenuItem, "children" | "id" | "to"> {
  id: string;
  /** Resolved internal href or the authored external URL. */
  to?: string;
  children?: readonly ComposedMenuItem[];
}

/** Field-widget registry: widget id -> renderer (opaque to the headless SDK). */
export type WidgetMap = Record<string, unknown>;

/**
 * Per-resource form contribution. Direct elements retain create-only override
 * compatibility; complete registrations are interpreted by the rendered binding.
 */
export interface RuntimeFormRegistration {
  resource: string;
  Component: unknown;
}

export type FormOverrideMap = Record<string, ReactElement | RuntimeFormRegistration>;

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
  canonicalLabel?: string;
  recordParam?: string;
}

/** Runtime context for rendering a chatter tab on the active page. */
export interface ChatterViewContext {
  pathname: string;
  params: Readonly<Record<string, string>>;
  route?: ChatterRoute;
  view: ChatterView;
}

/**
 * A chatter aside tab: a child of `record#aside` (every record view) or
 * `<model>#aside` (that model's pages), ordered by its `sequence`.
 */
export interface ChatterTabContent {
  /** Additional declarative scope evaluated by the shell before counts or rendering. */
  when?: (context: ChatterViewContext) => boolean;
  label?: ReactNode;
  icon?: string;
  count?: number;
  useCount?: (context: ChatterViewContext) => number | undefined;
  panelClassName?: string;
  render?: (context: ChatterViewContext) => ReactNode;
  /** Earlier ids a `?chatterTab=` link may still carry, kept for one release. */
  aliases?: readonly string[];
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
 * A docked drawer as the shell renders it: a `shell#drawers-<edge>` child's id,
 * sequence and content (`DockedDrawerContent`), with its edge. Drawers are
 * edge stripe-tabs, sticky across navigation and tabbed; the shell renders
 * `render()` into a plain edge-anchored panel, with no scrim and no focus trap.
 */
export interface DrawerContribution {
  id: string;
  edge: DrawerEdge;
  title: string;
  icon?: string;
  sequence?: number;
  render: () => ReactNode;
}

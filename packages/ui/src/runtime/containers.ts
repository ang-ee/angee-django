import { useMemo, type ComponentType, type ReactNode } from "react";
import { holdsPermission, type Row } from "@angee/metadata";

import { developmentMode } from "../lib/development-mode";
import { orderById, positionSiblings } from "../lib/position";
import type { BuiltInResourceViewKind } from "../views/resource/model/capabilities";
import type { ResourceViewKindCapabilities } from "../views/resource/model/capabilities";
import type { ChatterTabContent, DrawerContribution, DrawerEdge } from "./contracts";
import { isPresent, sessionPermitted, useAppRuntime } from "./runtime";

/**
 * Container names and the content their children carry. Each container's owner
 * adds its name by declaration merging, so a `containers` entry is type-checked
 * by the name after `#` in its address.
 */
export interface ContainerKinds {
  /** A record form's Group, Action and Tab declarations. */
  sections: ReactNode;
  /** RecordRailGroup declarations beside a saved record's tabs. */
  rail: ReactNode;
  /** Record verbs in a saved form's toolbar. */
  actions: ReactNode;
  /** Record verbs in a saved form's overflow menu. */
  "actions-menu": ReactNode;
  /** Passive record chrome at the right edge of a saved form's toolbar. */
  chrome: ReactNode;
  /** The view kinds a resource collection's switcher offers. */
  views: ResourceViewKindContent;
  /** Collection utilities beside a resource view's toolbar. */
  utilities: ReactNode;
  /** Notices below the console navigation, above the page controls. */
  notices: ReactNode;
  /** Items in the user menu, between the theme item and sign-out. */
  "user-menu": ReactNode;
  /** The console chrome's optional regions (`chrome.app-menu`, `chrome.breadcrumbs`); a region shows while its child does. */
  regions: null;
  /** Non-modal drawers docked on the console's right or bottom edge. */
  "drawers-right": DockedDrawerContent;
  "drawers-bottom": DockedDrawerContent;
  /** Chatter aside tabs. */
  aside: ChatterTabContent;
  /**
   * Renderable children of addon pages' own containers, declared on the
   * owner's node: toolbar verbs (`messaging.channels#toolbar`), dashboard
   * items, form and list declarations (`#fields`, `#facets`, `#columns`),
   * settings tools, and a decision's content and origin links.
   */
  toolbar: ReactNode;
  items: ReactNode;
  fields: ReactNode;
  facets: ReactNode;
  columns: ReactNode;
  tools: ReactNode;
  content: ReactNode;
  origin: ReactNode;
  /** The login page's sign-in methods (`auth.login#method`). */
  method: ReactNode;
  /** The login page's password help (`auth.login#password-help`). */
  "password-help": ReactNode;
}

/** A docked drawer: its stripe-tab title and glyph, and the panel it renders. */
export interface DockedDrawerContent {
  title: string;
  icon?: string;
  render: () => ReactNode;
}

/** A resource view kind as the switcher offers it and the collection renders it. */
export interface ResourceViewKindContent {
  /** The switcher's accessible label: a ui message key for the framework's kinds, text for contributed ones. */
  labelKey?: string;
  label?: string;
  icon: string;
  /** Which collection controls (filter, grouping, pager, columns) apply while it is active. */
  capabilities: ResourceViewKindCapabilities;
  /** A contributed kind's body; it reads the collection's filter and state through `useResourceView()`. */
  render?: ComponentType<{ resource: string }>;
}

/** When a render-time verb applies: an app or route on the active trail. */
export interface ContainerCondition {
  app?: string | readonly string[];
  /** The route or any route below it. */
  route?: string | readonly string[];
}

/** One child of a container: the owner's typed content plus the fields every container honours. */
export interface ContainerChild<TContent = unknown> {
  content: TContent;
  sequence?: number;
  before?: string;
  after?: string;
  /** A projected record permission the row must hold. */
  permission?: string;
  /**
   * Presence: `<app_label.ModelName>#<permission>` the session's identity must
   * hold (`current_user.permitted`), whatever the row; else the child is absent.
   */
  requires?: string;
  /** Readable fields the child consumes from the record. */
  requiredFields?: readonly string[];
  /** Shown only on rows whose implementation (`ImplClassField` value) is this one. */
  impl?: string;
  /** A per-row override (G-13): shown in place of `of` where the row's implementation is `impl`. */
  variant?: { of: string; impl: string };
  /** The owner's lookup key, for a container rendering one child per key. */
  key?: string;
}

/** A dependent's change to a child another addon declared. */
export interface ContainerAlteration {
  sequence?: number;
  before?: string;
  after?: string;
  /** Composition: the child leaves the container. */
  remove?: true;
  /** Render: hide the child, or show again what a dependency hid. */
  hide?: boolean;
}

/** One entry for an address: children keyed by id, plus the container-level verbs. */
export type ContainerEntry<TContent> = {
  /** Keep only these children (narrowing across layers, G-14). */
  only?: readonly string[];
  /** Drop these children. */
  except?: readonly string[];
  /**
   * The deployment only (`ANGEE_UI.containers`), with `only`: force it (G-14), so it
   * stands in for every addon's `only` and `except` instead of narrowing them.
   */
  force?: true;
  /** Where the entry's render verbs (`only`, `except`, `hide`) apply. */
  when?: ContainerCondition;
  /** On an addon's own container: at most one child per `key`. */
  unique?: "key";
  /** On an addon's own container: also addressed per model (`<model>#<name>`), inheriting along MTI parents. */
  models?: true;
} & { readonly [child: `${string}.${string}`]: ContainerChild<TContent> | ContainerAlteration }
  // The framework's own children keep bare ids (the built-in view kinds); any layer may adjust them.
  & { readonly [child in BuiltInResourceViewKind]?: ContainerAlteration };

/** An addon's `containers`: addresses (`node#name`) to an entry or conditional alternatives. */
export type ContainersDeclaration = {
  readonly [K in keyof ContainerKinds as `${string}#${K & string}`]?:
    ContainerEntry<ContainerKinds[K]> | readonly ContainerEntry<ContainerKinds[K]>[];
};

/** A container the framework owns, addressed at its kind (`form#sections`) and per model when `models`. */
export interface CoreContainer {
  address: `${string}#${string}`;
  models?: boolean;
  /** Children the framework itself declares, under bare ids (`list`, `board`) its owner already persists. */
  children?: Readonly<Record<string, ContainerChild>>;
  /** The owner also adds children at render time (a page's chatter tabs), so `only` may name ids composition cannot know. */
  extras?: true;
}

/** A child as composed: its id, its declaring addon and the address it was declared at. */
export interface ComposedContainerChild<TContent = unknown> extends ContainerChild<TContent> {
  id: string;
  owner: string;
  address: string;
}

/** A render-time narrowing one layer declared on one address. */
export interface ContainerRule {
  layer: string;
  /** Dependency rank: rules apply in this order, so a dependent's `hide: false` follows its dependency's `hide`. */
  rank: number;
  when?: ContainerCondition;
  only?: readonly string[];
  except?: readonly string[];
  hide?: readonly string[];
  show?: readonly string[];
  /** Layers whose children this rule's `only` never filters: the layer's dependents (G2.2). */
  exempt: readonly string[];
  /**
   * A deployment `only` declared with `force: true` (G-14 "the deployment may
   * force"): where it holds, the deployment's `only` and `except` stand in for
   * every layer's at the addresses a page merges, so it may admit a child an
   * addon's `only` left out. `hide` still applies; un-hide it.
   */
  force?: true;
}

/** The composed containers the runtime renders from. */
export interface ComposedContainers {
  /** Every container: its owner (`framework` for core ones) and whether it takes model addresses. */
  declared: Readonly<Record<string, { owner: string; models: boolean; unique?: "key"; extras?: true }>>;
  children: Readonly<Record<string, readonly ComposedContainerChild[]>>;
  rules: Readonly<Record<string, readonly ContainerRule[]>>;
  /** Children a layer removed, and who removed them. */
  removed: readonly { address: string; id: string; by: string }[];
  /** The layer behind each child field, keyed `address/id`. */
  provenance: Readonly<Record<string, Readonly<Record<string, string>>>>;
  /** Non-fatal findings, such as a position against a child the container does not hold. */
  diagnostics: readonly string[];
}

/** Where the page is, for `when`: its apps and its routes. */
export interface ContainerScope {
  /** Every app on the page's menu trail, outermost first, flattened ones too (G-8). */
  apps: readonly string[];
  /** The page's route and its parent routes, nearest first. */
  routes: readonly string[];
}

const warnedAddresses = new Set<string>();

export const EMPTY_CONTAINERS: ComposedContainers = { declared: {}, children: {}, rules: {}, removed: [], provenance: {}, diagnostics: [] };

/**
 * Children placed straight into the runtime shape, with no layering: for
 * stories and tests. An app composes its addons' containers through
 * `@angee/app`, which validates and layers them. A child's owner is its id's
 * namespace.
 */
export function containersFromChildren(
  core: readonly CoreContainer[],
  children: Readonly<Record<string, Readonly<Record<string, ContainerChild>>>>,
): ComposedContainers {
  const composed: Record<string, ComposedContainerChild[]> = {};
  for (const container of core) {
    for (const [id, child] of Object.entries(container.children ?? {})) {
      (composed[container.address] ??= []).push({ ...child, id, owner: "framework", address: container.address });
    }
  }
  for (const [address, byId] of Object.entries(children)) {
    for (const [id, child] of Object.entries(byId)) (composed[address] ??= []).push({ ...child, id, owner: id.split(".")[0]!, address });
  }
  return {
    ...EMPTY_CONTAINERS,
    declared: Object.fromEntries(core.map((container) => [container.address,
      { owner: "framework", models: container.models === true, ...(container.extras ? { extras: true as const } : {}) }])),
    children: composed,
  };
}
const EMPTY_SCOPE: ContainerScope = { apps: [], routes: [] };

/** Split an address into its node and container name. */
export function containerName(address: string): string {
  const at = address.indexOf("#");
  if (at <= 0 || at === address.length - 1) throw new Error(`Container address "${address}" must be "node#name".`);
  return address.slice(at + 1);
}

export interface ResolveContainerOptions {
  /** The record's models, most general first (the canonical model, then the concrete one). */
  models?: readonly string[];
  scope?: ContainerScope;
  /** The record the children render for; `permission` is checked against it when given. */
  row?: Row | null;
  /** The row's implementation keys (`ImplClassField` values); variants for them replace their originals. */
  impls?: readonly string[];
  /**
   * Children a page adds at render time (a page's published chatter tabs). They
   * interleave with composed children by `sequence`; without a position they
   * trail in input order. `before`/`after` may anchor on composed ids. They get
   * the same `only`/`except` narrowing; `hide` alters composed children only.
   */
  extra?: readonly ComposedContainerChild[];
  /**
   * Every child the layers admit, whatever the row: `impl` children and all
   * variants included, no permission check. A record's field projection reads it.
   */
  projection?: boolean;
  /**
   * The `requires` refs the session holds: a child requiring another is absent,
   * its variants with it, in projections too. Omitted, declarations stand.
   */
  permitted?: readonly string[];
}

/** A record's models for a container, most general first: its MTI parent, then itself. */
export function modelChain(canonical: string | null | undefined, model: string | null | undefined): readonly string[] {
  return [...new Set([canonical, model].filter((label): label is string => Boolean(label)))];
}

function matches(condition: ContainerCondition | undefined, scope: ContainerScope): boolean {
  if (!condition) return true;
  const any = (wanted: string | readonly string[] | undefined, present: readonly string[]): boolean =>
    wanted === undefined || (typeof wanted === "string" ? [wanted] : wanted).some((id) => present.includes(id));
  return any(condition.app, scope.apps) && any(condition.route, scope.routes);
}

/**
 * The children of a container as one page renders them: the kind-level address
 * and each model's address merged with render-time extras and positioned by
 * sequence. Ties keep composed children in id order, then extras in input order;
 * unpositioned extras trail, and `before`/`after` may anchor on composed ids.
 * Then every layer's narrowing whose condition holds applies in dependency
 * order, with variants standing in for their originals on matching rows and the row's
 * permission last. A child whose `requires` the session lacks is absent, with its
 * variants, as a hidden one is. Narrowing by a variant's own id drops that variant (its
 * original returns); narrowing by an original's id carries its variants along.
 * A deployment `only` that holds forces: its narrowing replaces the addons'.
 * `projection` keeps every candidate (impl children and all variants) for the
 * fields to fetch, each unplaced variant right after its original.
 */
export function resolveContainer<TContent = unknown>(
  composed: ComposedContainers,
  address: string,
  { models = [], scope = EMPTY_SCOPE, row, impls = [], extra = [], projection = false, permitted }: ResolveContainerOptions = {},
): readonly ComposedContainerChild<TContent>[] {
  // Composition validates every address; a runtime composed without this container
  // (a story, a bare test) has no composed children for it, only the page's own.
  const declared = composed.declared[address];
  if (!declared) return positionSiblings(extra, `Children of "${address}"`) as readonly ComposedContainerChild<TContent>[];
  const name = containerName(address);
  const addresses = declared.models ? [address, ...models.map((model) => `${model}#${name}`)] : [address];
  // One id on a model and on its MTI parent (one addon's, as ids are namespaced): the model's own stands.
  const byId = new Map(addresses.flatMap((at) => composed.children[at] ?? []).map((child) => [child.id, child]));
  const merged = positionSiblings([...orderById([...byId.values()]), ...extra], `Children of "${address}"`);
  const rules = addresses.flatMap((at) => composed.rules[at] ?? [])
    .filter((rule) => matches(rule.when, scope))
    .map((rule, index) => ({ rule, index }))
    .sort((left, right) => left.rule.rank - right.rule.rank || left.index - right.index)
    .map(({ rule }) => rule);
  const hidden = new Set<string>();
  for (const rule of rules) {
    for (const id of rule.hide ?? []) hidden.add(id);
    for (const id of rule.show ?? []) hidden.delete(id);
  }
  // Where the deployment forces (G-14), its `only` and `except` stand in for every layer's.
  const forcing = rules.find((rule) => rule.force)?.layer;
  const narrowing = forcing === undefined ? rules : rules.filter((rule) => rule.layer === forcing);
  const excepted = new Set(narrowing.flatMap((rule) => rule.except ?? []));
  // Presence: a child whose `requires` the session lacks is absent, as a hidden one is.
  const absent = new Set(merged.filter((child) => !isPresent(child.requires, permitted)).map((child) => child.id));
  const blocked = (id: string): boolean => hidden.has(id) || excepted.has(id) || absent.has(id);

  // A variant carries its original's admission: its id and its owner for `only`'s exemption.
  const originals = new Map(merged.map((child) => [child.id, child]));
  const lineage = (child: ComposedContainerChild): string => child.variant?.of ?? child.id;
  const ownerOf = (child: ComposedContainerChild): string => originals.get(lineage(child))?.owner ?? child.owner;
  const permitted = (child: ComposedContainerChild): boolean =>
    projection || row === undefined || !child.permission || holdsPermission(row, child.permission);
  // A variant positioned in its own right keeps its place; one without stands where
  // its original stood, or keeps its own when the original is not on this page.
  const placed = (child: ComposedContainerChild): boolean =>
    child.sequence !== undefined || child.before !== undefined || child.after !== undefined
    || (child.variant !== undefined && !originals.has(child.variant.of));

  let visible = merged.filter((child) => !blocked(child.id) && !blocked(lineage(child)));
  if (projection) {
    // Every candidate stays; an unplaced variant follows its original, whose place it takes on its rows.
    const following = new Map<string, ComposedContainerChild[]>();
    const trails = (child: ComposedContainerChild): boolean => child.variant !== undefined && !placed(child);
    for (const child of visible) {
      if (trails(child)) following.set(child.variant!.of, [...(following.get(child.variant!.of) ?? []), child]);
    }
    visible = visible.filter((child) => !trails(child)).flatMap((child) => [child, ...(following.get(child.id) ?? [])]);
  } else {
    // Variants for the row's implementation stand in for their originals; one the
    // row lacks the permission for leaves the original in place (G-13).
    const variants = new Map<string, ComposedContainerChild>();
    for (const child of visible) {
      if (!child.variant || !impls.includes(child.variant.impl) || !permitted(child)) continue;
      const taken = variants.get(child.variant.of);
      if (taken) throw new Error(`Children "${taken.id}" and "${child.id}" of "${address}" are both variants of "${child.variant.of}" for this row.`);
      variants.set(child.variant.of, child);
    }
    visible = visible
      .filter((child) => !child.variant || (variants.get(child.variant.of) === child && placed(child)))
      .flatMap((child) => {
        const variant = variants.get(child.id);
        if (!variant) return [child];
        return placed(variant) ? [] : [variant];
      })
      .filter((child) => child.impl === undefined || impls.includes(child.impl));
  }
  for (const rule of narrowing) {
    if (!rule.only) continue;
    const kept = new Set(rule.only);
    visible = visible.filter((child) => kept.has(child.id) || kept.has(lineage(child)) || rule.exempt.includes(ownerOf(child)));
  }
  return visible.filter(permitted) as readonly ComposedContainerChild<TContent>[];
}

/**
 * The children of one container for the current page: the app and route come
 * from the runtime, presence from the session's identity, the models and row
 * from the rendering owner. Memoize `models` and `impls`; a fresh array each
 * render recomputes the list.
 */
export function useContainer<TContent = unknown>(
  address: string,
  options: Omit<ResolveContainerOptions, "scope" | "permitted"> = {},
): readonly ComposedContainerChild<TContent>[] {
  const { containers = EMPTY_CONTAINERS, containerScope, auth } = useAppRuntime();
  const { models, row, impls, extra, projection } = options;
  const permitted = sessionPermitted(auth.user);
  if (developmentMode() && containers !== EMPTY_CONTAINERS && !containers.declared[address] && !warnedAddresses.has(address)) {
    warnedAddresses.add(address);
    console.warn(`[angee] useContainer("${address}"): no such container was composed.`);
  }
  return useMemo(
    () => resolveContainer<TContent>(containers, address, { models, row, impls, extra, projection, permitted, ...(containerScope ? { scope: containerScope } : {}) }),
    [address, containerScope, containers, extra, impls, models, permitted, projection, row],
  );
}

/** The drawers docked on one edge (`shell#drawers-<edge>`), in composed order. */
export function useDrawers(edge: DrawerEdge): readonly DrawerContribution[] {
  const children = useContainer<DockedDrawerContent>(`shell#drawers-${edge}`);
  return useMemo(
    () => children.map(({ id, sequence, content }) => ({ id, edge, ...(sequence !== undefined ? { sequence } : {}), ...content })),
    [children, edge],
  );
}

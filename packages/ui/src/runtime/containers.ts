import { useMemo, type ComponentType, type ReactNode } from "react";
import { holdsPermission, type Row } from "@angee/metadata";

import { positionSiblings } from "../lib/position";
import type { ResourceViewKindCapabilities } from "../views/resource/model/capabilities";
import type { ChatterTabContent, DrawerContribution, DrawerEdge } from "./contracts";
import { useAppRuntime } from "./runtime";

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

/** When a render-time verb applies: an app or route on the active trail, or the perspective. */
export interface ContainerCondition {
  app?: string | readonly string[];
  /** The route or any route below it. */
  route?: string | readonly string[];
  perspective?: string | readonly string[];
}

/** One child of a container: the owner's typed content plus the fields every container honours. */
export interface ContainerChild<TContent = unknown> {
  content: TContent;
  sequence?: number;
  before?: string;
  after?: string;
  /** A projected record permission the row must hold. */
  permission?: string;
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
  /** Where the entry's render verbs (`only`, `except`, `hide`) apply. */
  when?: ContainerCondition;
  /** On an addon's own container: at most one child per `key`. */
  unique?: "key";
  /** On an addon's own container: also addressed per model (`<model>#<name>`), inheriting along MTI parents. */
  models?: true;
} & { readonly [child: `${string}.${string}`]: ContainerChild<TContent> | ContainerAlteration };

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
  when?: ContainerCondition;
  only?: readonly string[];
  except?: readonly string[];
  hide?: readonly string[];
  show?: readonly string[];
  /** Layers whose children this rule's `only` never filters: the layer's dependents (G2.2). */
  exempt: readonly string[];
}

/** The composed containers the runtime renders from. */
export interface ComposedContainers {
  /** Every container: its owner (`framework` for core ones) and whether it takes model addresses. */
  declared: Readonly<Record<string, { owner: string; models: boolean; unique?: "key" }>>;
  children: Readonly<Record<string, readonly ComposedContainerChild[]>>;
  rules: Readonly<Record<string, readonly ContainerRule[]>>;
  /** Children a layer removed, and who removed them. */
  removed: readonly { address: string; id: string; by: string }[];
  /** The layer behind each child field, keyed `address/id`. */
  provenance: Readonly<Record<string, Readonly<Record<string, string>>>>;
  diagnostics: readonly string[];
}

/** Where the page is: the apps and routes on its trail (nearest first) and the perspective. */
export interface ContainerScope {
  apps: readonly string[];
  routes: readonly string[];
  perspective: string | null;
}

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
  return {
    ...EMPTY_CONTAINERS,
    declared: Object.fromEntries(core.map((container) => [container.address, { owner: "framework", models: container.models === true }])),
    children: {
      ...Object.fromEntries(core.filter((container) => container.children).map((container) => [container.address,
        Object.entries(container.children!).map(([id, child]) => ({ ...child, id, owner: "framework", address: container.address }))])),
      ...Object.fromEntries(Object.entries(children).map(([address, byId]) => [address,
        Object.entries(byId).map(([id, child]) => ({ ...child, id, owner: id.split(".")[0]!, address }))])),
    },
  };
}
const EMPTY_SCOPE: ContainerScope = { apps: [], routes: [], perspective: null };

/** Split an address into its node and container name. */
export function containerName(address: string): string {
  const at = address.indexOf("#");
  if (at <= 0 || at === address.length - 1) throw new Error(`Container address "${address}" must be "node#name".`);
  return address.slice(at + 1);
}

function matches(condition: ContainerCondition | undefined, scope: ContainerScope): boolean {
  if (!condition) return true;
  const any = (wanted: string | readonly string[] | undefined, present: readonly string[]): boolean =>
    wanted === undefined || (typeof wanted === "string" ? [wanted] : wanted).some((id) => present.includes(id));
  return any(condition.app, scope.apps) && any(condition.route, scope.routes)
    && any(condition.perspective, scope.perspective ? [scope.perspective] : []);
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
   * follow the composed ones and get the same narrowing.
   */
  extra?: readonly ComposedContainerChild[];
}

/**
 * The children of a container as one page renders them: the kind-level address
 * and each model's address merged and positioned, variants applied for the row,
 * then every layer's narrowing whose condition holds, then the row's permission.
 */
export function resolveContainer<TContent = unknown>(
  composed: ComposedContainers,
  address: string,
  { models = [], scope = EMPTY_SCOPE, row, impls = [], extra = [] }: ResolveContainerOptions = {},
): readonly ComposedContainerChild<TContent>[] {
  // Composition validates every address; a runtime composed without this container (a story, a bare test) has no children for it.
  const declared = composed.declared[address];
  if (!declared) return [];
  const name = containerName(address);
  const addresses = declared.models ? [address, ...models.map((model) => `${model}#${name}`)] : [address];
  const merged = [
    ...positionSiblings(addresses.flatMap((at) => composed.children[at] ?? []), `Children of "${address}"`),
    ...extra,
  ];

  // Variants for the row's implementation stand in for their originals; one the
  // row lacks the permission for leaves the original in place (G-13).
  const permitted = (child: ComposedContainerChild): boolean =>
    row === undefined || !child.permission || holdsPermission(row, child.permission);
  const variants = new Map<string, ComposedContainerChild>();
  for (const child of merged) {
    if (!child.variant || !impls.includes(child.variant.impl) || !permitted(child)) continue;
    const taken = variants.get(child.variant.of);
    if (taken) throw new Error(`Children "${taken.id}" and "${child.id}" of "${address}" are both variants of "${child.variant.of}" for this row.`);
    variants.set(child.variant.of, child);
  }
  // A variant carries its original's admission: its id and its owner for `only`'s exemption.
  const originals = new Map(merged.map((child) => [child.id, child]));
  const lineage = (child: ComposedContainerChild): string => child.variant?.of ?? child.id;
  const ownerOf = (child: ComposedContainerChild): string => originals.get(lineage(child))?.owner ?? child.owner;
  let visible = merged.filter((child) => !child.variant || variants.get(child.variant.of) === child)
    .filter((child) => !variants.has(child.id));

  const hidden = new Set<string>();
  for (const at of addresses) {
    for (const rule of composed.rules[at] ?? []) {
      if (!matches(rule.when, scope)) continue;
      if (rule.only) {
        const kept = new Set(rule.only);
        visible = visible.filter((child) => kept.has(lineage(child)) || rule.exempt.includes(ownerOf(child)));
      }
      if (rule.except) visible = visible.filter((child) => !rule.except!.includes(lineage(child)));
      for (const id of rule.hide ?? []) hidden.add(id);
      for (const id of rule.show ?? []) hidden.delete(id);
    }
  }
  visible = visible.filter((child) => !hidden.has(lineage(child)));
  visible = visible.filter((child) => child.impl === undefined || impls.includes(child.impl));
  visible = visible.filter(permitted);
  return visible as readonly ComposedContainerChild<TContent>[];
}

/**
 * The children of one container for the current page: the app and route come
 * from the runtime, the models and row from the rendering owner. Memoize
 * `models` and `impls`; a fresh array each render recomputes the list.
 */
export function useContainer<TContent = unknown>(
  address: string,
  options: Omit<ResolveContainerOptions, "scope"> = {},
): readonly ComposedContainerChild<TContent>[] {
  const { containers = EMPTY_CONTAINERS, containerScope } = useAppRuntime();
  const { models, row, impls, extra } = options;
  return useMemo(
    () => resolveContainer<TContent>(containers, address, { models, row, impls, extra, ...(containerScope ? { scope: containerScope } : {}) }),
    [address, containerScope, containers, extra, impls, models, row],
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

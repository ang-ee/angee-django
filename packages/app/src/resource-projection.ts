import {
  dataResourcesFromAngeeSchemaMetadata,
  refineResourcesFromDataResources,
  refineRoutePathForTanStack,
  type AngeeSchemaMetadata,
  type RefineResourceMetadata,
} from "@angee/metadata";
import type { ResourceProps } from "@refinedev/core";
import type { ResourceMutationOperations } from "@angee/refine";
import {
  MenuTree,
  pathMatchesTarget,
  type ChromeMenuNode,
  type MenuMatch,
} from "@angee/ui/chrome/menu-tree";
import type { RuntimeResourceRoutes } from "@angee/ui/runtime";

import type { BaseAddonRoute } from "./define-base-addon";
import {
  childRoutesByParentName,
  fullRoutePath,
  inheritedRouteFact,
  routeChildHasTrailingParam,
  trailingRouteParamName,
} from "./route-paths";

interface SchemaWithMetadata {
  metadata?: AngeeSchemaMetadata;
}

/** Translate metadata once; the provider receives only executable wire facts. */
export function resourceMutationsForSchema(
  metadata: AngeeSchemaMetadata | undefined,
): Readonly<Record<string, ResourceMutationOperations>> {
  return Object.fromEntries(dataResourcesFromAngeeSchemaMetadata(metadata).flatMap((resource) => {
    if (!resource.roots.list) return [];
    const mutations: ResourceMutationOperations = {};
    if (resource.roots.create && resource.typeNames.createInput && resource.createArguments?.length) {
      mutations.create = { root: resource.roots.create, inputType: resource.typeNames.createInput, arguments: resource.createArguments };
    }
    if (resource.roots.update && resource.typeNames.updateInput && resource.updateArguments?.length) {
      mutations.update = { root: resource.roots.update, inputType: resource.typeNames.updateInput, arguments: resource.updateArguments };
    }
    return [[resource.roots.list, mutations] as const];
  }));
}

export interface RefineRouteResourceProjection {
  resources: readonly ResourceProps[];
  metadataByResource: Readonly<Record<string, RefineResourceMetadata>>;
}

export function refineResourcesForSchemas(
  schemas: Readonly<Record<string, SchemaWithMetadata>>,
  routesByResource: Readonly<Record<string, string>>,
  metadataByResource: Readonly<Record<string, RefineResourceMetadata>>,
): readonly ResourceProps[] {
  return Object.values(schemas).flatMap((schema) =>
    refineResourcesFromDataResources(
      dataResourcesFromAngeeSchemaMetadata(schema.metadata),
      {
        pathsByResource: routesByResource,
        metadataByResource,
      },
    ),
  );
}

export function refineRouteResourceProjection(
  routes: readonly BaseAddonRoute[],
  menuTree: MenuTree,
  navigationTree: MenuTree = menuTree,
  selectedRoutes?: Readonly<Record<string, RuntimeResourceRoutes>>,
): RefineRouteResourceProjection {
  const resourcesByIdentifier = new Map<string, ResourceProps>();
  const metadataByResource: Record<string, RefineResourceMetadata> = {};
  const appRootIds = new Set(navigationTree.appRoots().map((item) => item.id));
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const childrenByParentName = childRoutesByParentName(routes);

  for (const node of navigationTree.byId.values()) {
    const menuTrail = navigationTree.trailFor(node.id);
    menuTrail.forEach((item, index) => {
      const breadcrumbTrail = breadcrumbTrailFromMenuTrail(menuTrail.slice(0, index + 1));
      addMenuRouteResource(
        resourcesByIdentifier,
        item,
        menuTrail[index - 1],
        appRootIds.has(item.id),
        menuRouteShowPath(item, routesByName, childrenByParentName),
        breadcrumbTrail.at(-2),
      );
    });
  }

  for (const route of routes) {
    const resource = route.resource ?? route.recordModel;
    if (!resource || (selectedRoutes && selectedRoutes[resource]?.collection !== route.name)) continue;
    const selected = menuNodeForRoute(route, menuTree);
    const trail = selected
      ? breadcrumbTrailFromMenuTrail(menuTree.trailFor(selected.id))
      : [];
    const leaf = trail.at(-1);
    const parent = trail.length > 1 ? trail[trail.length - 2] : undefined;
    metadataByResource[resource] = {
      ...(leaf ? { label: leaf.displayLabel } : routeLabel(route)),
      ...(leaf ? { icon: leaf.iconName } : routeIcon(route)),
      ...(parent ? { parent: menuRouteResourceIdentifier(parent.id) } : {}),
    };
  }

  return {
    resources: [...resourcesByIdentifier.values()],
    metadataByResource,
  };
}

/**
 * Index each resource to the collection and record route names derived from its
 * route declarations. A resource may be claimed by only one route; a second
 * claim is a build-time error, matching the registry collision discipline
 * elsewhere.
 */
export function resourceRouteIndex(
  routes: readonly BaseAddonRoute[],
): Record<string, RuntimeResourceRoutes> {
  const byResource: Record<string, RuntimeResourceRoutes> = {};
  const childrenByParentName = childRoutesByParentName(routes);
  for (const route of routes) {
    if (!route.resource) continue;
    if (route.path.includes("$")) {
      throw new Error(
        `Route "${route.name}" claims resource "${route.resource}" but its ` +
          `path "${route.path}" is parameterized — a resource's collection ` +
          `href must resolve with no params at boot. Parameterized pages ` +
          `are projections: drop the resource claim and scope the page's ` +
          `own surface instead.`,
      );
    }
    if (Object.prototype.hasOwnProperty.call(byResource, route.resource)) {
      throw new Error(
        `Route "${route.name}" claims resource "${route.resource}" already claimed by another route.`,
      );
    }
    const recordRoutes = (childrenByParentName.get(route.name) ?? [])
      .filter((candidate) => routeChildHasTrailingParam(candidate, route));
    if (recordRoutes.length > 1) throw new Error(`Route "${route.name}" has multiple record routes.`);
    const recordRoute = recordRoutes[0];
    const recordParam = recordRoute
      ? trailingRouteParamName(recordRoute.path)
      : undefined;
    byResource[route.resource] = {
      collection: route.name,
      ...(recordRoute && recordParam
        ? { record: { name: recordRoute.name, param: recordParam } }
        : {}),
    };
  }
  return byResource;
}

/** A menu node that left: composition removed it, or the actor lacks a capability it needs. */
export interface MenuNodeLeft {
  id: string;
  route?: string;
  /** Why, as the predicate of "menu item … "; defaults to "was removed". */
  reason?: string;
}

/** What an `AppRouteProjection` projects beyond its routes, logical tree and confinement. */
export interface AppRouteProjectionOptions {
  /** What the chrome shows; defaults to the logical tree. */
  navigation?: MenuTree;
  /** The menu nodes composition removed, which decides route availability. */
  removed?: readonly MenuNodeLeft[];
  /**
   * The actor's capabilities: menu nodes and routes requiring another one leave.
   * Omitted, the composition is projected as declared and nothing is gated.
   */
  capabilities?: ReadonlySet<string>;
}

/** A capability name as IAM declares one: a permission identifier on `iam/capability`. */
const CAPABILITY_NAME = /^[a-z][a-z0-9_]*$/;

/** One projection for app resource claims, navigation membership and admission. */
export class AppRouteProjection {
  /** The logical tree (route ownership, trails) less the nodes the actor lacks a capability for. */
  readonly menuTree: MenuTree;
  readonly navigationTree: MenuTree;
  readonly canonical: Readonly<Record<string, RuntimeResourceRoutes>>;
  /** Capability names the menu nodes and routes require, each with the declarations requiring it. */
  readonly requirements: ReadonlyMap<string, readonly string[]>;
  private readonly roots = new Map<string, string | undefined>();
  private readonly claims = new Map<string, Map<string, RuntimeResourceRoutes[]>>();
  private readonly recordDestinations = new Map<string, Map<string, NonNullable<RuntimeResourceRoutes["recordDestinations"]>>>();
  private readonly routesByName: ReadonlyMap<string, BaseAddonRoute>;
  private readonly byCapabilities = new Map<string, AppRouteProjection>();

  /** Routes removed menu nodes, or capabilities the actor lacks, made unavailable, with the reason; they redirect home. */
  readonly unavailable: ReadonlyMap<string, string>;

  constructor(
    readonly routes: readonly BaseAddonRoute[],
    private readonly declaredMenuTree: MenuTree,
    readonly confineTo?: string,
    private readonly options: AppRouteProjectionOptions = {},
  ) {
    this.requirements = capabilityRequirements(routes, declaredMenuTree);
    const denied = options.capabilities
      ? deniedMenuNodes(declaredMenuTree, options.capabilities, gatedRoutes(routes, options.capabilities))
      : [];
    const deniedIds = new Set(denied.map((node) => node.id));
    if (confineTo !== undefined && deniedIds.has(confineTo)) {
      throw new Error(`Menu root "${confineTo}" confines the console, so it cannot require a capability.`);
    }
    this.menuTree = declaredMenuTree.without(deniedIds);
    const menuTree = this.menuTree;
    const navigation = (options.navigation ?? declaredMenuTree).without(deniedIds);
    this.navigationTree = confineTo === undefined ? navigation.withSettingsPlace() : navigation.confineTo(confineTo);
    this.unavailable = unavailableRoutes(routes, menuTree, [...(options.removed ?? []), ...denied], options.capabilities);
    this.routesByName = new Map(routes.map((route) => [route.name, route]));
    const appIds = new Set(menuTree.roots.filter((root) => root.appRoot === true || root.id === confineTo).map((root) => root.id));
    const canonical: BaseAddonRoute[] = [];
    // Menu order selects the app's fallback when it has several views of a model.
    const order = [...menuTree.byId.values()].map((item) => item.route);
    const ordered = [...routes].sort((a, b) => {
      const left = order.indexOf(a.name);
      const right = order.indexOf(b.name);
      return (left < 0 ? Infinity : left) - (right < 0 ? Infinity : right)
        || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
    });
    for (const route of ordered) {
      if (this.unavailable.has(route.name)) continue;
      const root = this.rootFor(route);
      const resource = route.resource ?? route.recordModel;
      if (!resource || !root || !appIds.has(root)) {
        canonical.push(route);
        continue;
      }
      if (route.path.includes("$")) {
        if (route.resource) resourceRouteIndex([route]); // Same collection contract in every scope.
        continue;
      }
      const claim = resourceRouteIndex([
        { ...route, resource },
        ...routes.filter((child) => child.parent === route.name),
      ])[resource]!;
      if (route.recordMatch && !claim.record) {
        throw new Error(`Route "${route.name}" declares recordMatch without a record child.`);
      }
      if (route.recordMatch && claim.record) {
        const byResource = this.recordDestinations.get(root) ?? new Map<string, NonNullable<RuntimeResourceRoutes["recordDestinations"]>>();
        const destinations = byResource.get(resource) ?? [];
        if (destinations.some((item) => item.match.field === route.recordMatch?.field && item.match.equals === route.recordMatch?.equals)) {
          throw new Error(`Resource "${resource}" has duplicate record match "${route.recordMatch.field}=${route.recordMatch.equals}".`);
        }
        byResource.set(resource, [...destinations, { record: claim.record, match: route.recordMatch }]);
        this.recordDestinations.set(root, byResource);
      }
      const resources = this.claims.get(root) ?? new Map<string, RuntimeResourceRoutes[]>();
      resources.set(resource, [...(resources.get(resource) ?? []), claim]);
      this.claims.set(root, resources);
    }
    this.canonical = resourceRouteIndex(canonical);
  }

  /**
   * This composition as an actor holding `capabilities` sees it: one projection
   * per capability set, built once. Without requirements it is this projection.
   */
  forCapabilities(capabilities: Iterable<string>): AppRouteProjection {
    if (!this.requirements.size) return this;
    const held = new Set(capabilities);
    const key = [...held].sort().join("\n");
    let projection = this.byCapabilities.get(key);
    if (!projection) {
      projection = new AppRouteProjection(this.routes, this.declaredMenuTree, this.confineTo, { ...this.options, capabilities: held });
      this.byCapabilities.set(key, projection);
    }
    return projection;
  }

  rootFor(route: BaseAddonRoute, visited = new Set<string>()): string | undefined {
    if (this.roots.has(route.name)) return this.roots.get(route.name);
    if (visited.has(route.name)) throw new Error(`Route "${route.name}" creates a parent cycle.`);
    visited.add(route.name);
    // Record children inherit their collection owner; an app's Settings link
    // admits that target without transferring ownership of the foreign route.
    const parent = route.parent ? this.routesByName.get(route.parent) : undefined;
    const selected = route.menu ? menuNodeForRoute(route, this.menuTree) : undefined;
    const refs = selected ? [selected] : this.menuTree.itemsForRoute(route.name);
    const roots = new Set(refs.map((item) => this.menuTree.trailFor(item.id)[0]?.id));
    if (roots.size > 1 && this.confineTo !== undefined && !parent) {
      throw new Error(`Route "${route.name}" is referenced by different menu roots; declare route.menu.`);
    }
    const root = parent && !selected ? this.rootFor(parent, visited)
      : roots.size === 1 ? roots.values().next().value : undefined;
    this.roots.set(route.name, root);
    return root;
  }

  /** Record routes inherit their collection's declared chrome anchor. */
  menuAnchor(routeName?: string): ChromeMenuNode | undefined {
    const route = routeName ? this.routesByName.get(routeName) : undefined;
    if (!route) return undefined;
    const owner = inheritedRouteFact(route, this.routesByName, (item) => item.menu ? item : undefined);
    return owner ? menuNodeForRoute(owner, this.menuTree) : undefined;
  }

  activeMenu(pathname: string, routeName?: string, search?: string): MenuMatch | undefined {
    return this.navigationTree.match(pathname, search, false, this.menuAnchor(routeName)?.id);
  }

  /** The logical root a page sits in; lifting a node into Settings does not change it. */
  activeApp(pathname: string, routeName?: string, search?: string): string | undefined {
    return this.confineTo ?? this.menuTree.match(pathname, search, false, this.menuAnchor(routeName)?.id)?.trail[0]?.id;
  }

  /**
   * The apps a container `when: { app }` matches on this path, outermost first:
   * every app on its menu trail. Under a confinement only the root's own count;
   * a page another root owns (a Settings link) sits in the root alone.
   */
  appTrail(pathname: string, routeName?: string, search?: string): readonly string[] {
    const trail = this.menuTree.appTrail(pathname, search, this.menuAnchor(routeName)?.id).map((item) => item.id);
    if (this.confineTo === undefined) return trail;
    return trail[0] === this.confineTo ? trail : [this.confineTo];
  }

  /** Collection defaults are inherited by its record children. */
  defaultResourceView(routeName?: string): string | undefined {
    const route = routeName ? this.routesByName.get(routeName) : undefined;
    return route ? inheritedRouteFact(route, this.routesByName, (item) => item.defaultResourceView) : undefined;
  }

  resourceRoutes(app?: string, activeRoute?: string): Readonly<Record<string, RuntimeResourceRoutes>> {
    const result = { ...this.canonical };
    const route = activeRoute ? this.routesByName.get(activeRoute) : undefined;
    for (const [resource, claims] of this.claims.get(app ?? "") ?? []) {
      const selected = claims.find((claim) => route && inheritedRouteFact(route, this.routesByName,
        (ancestor) => ancestor.name === claim.collection ? true : undefined)) ?? claims[0]!;
      const record = selected.record ?? claims.find((claim) => claim.record)?.record ?? this.canonical[resource]?.record;
      result[resource] = { ...selected, ...(record ? { record } : {}) };
    }
    for (const [resource, destinations] of this.recordDestinations.get(app ?? "") ?? []) {
      const selected = result[resource];
      if (!selected) continue;
      result[resource] = {
        ...selected,
        recordDestinations: destinations,
        ...(this.canonical[resource]?.record ? { recordFallback: this.canonical[resource].record } : {}),
      };
    }
    return result;
  }

  allows(route: BaseAddonRoute, pathname: string): boolean {
    if (this.unavailable.has(route.name)) return false;
    if (this.confineTo === undefined) return true;
    const root = this.rootFor(route);
    if (root === undefined || root === this.confineTo) return true;
    return [...this.navigationTree.byId.values()].some((item) => {
      const target = item.route ? this.routesByName.get(item.route) : undefined;
      return target && this.rootFor(target) !== this.confineTo
        && pathMatchesTarget(pathname, item.target);
    });
  }
}

function addMenuRouteResource(
  resourcesByIdentifier: Map<string, ResourceProps>,
  item: ChromeMenuNode,
  parent: ChromeMenuNode | undefined,
  appRoot: boolean,
  showPath: string | undefined,
  breadcrumbParent: ChromeMenuNode | undefined,
): void {
  const target = item.target;
  if (!target || target === "#") return;
  const identifier = menuRouteResourceIdentifier(item.id);
  const existing = resourcesByIdentifier.get(identifier);
  if (existing) {
    if (appRoot) {
      existing.meta = {
        ...existing.meta,
        appRoot: true,
      };
    }
    return;
  }
  resourcesByIdentifier.set(identifier, {
    name: identifier,
    identifier,
    list: refineRoutePathForTanStack(target),
    ...(showPath ? { show: refineRoutePathForTanStack(showPath) } : {}),
    meta: {
      label: item.displayLabel,
      icon: item.iconName,
      menuId: item.id,
      menuOrder: resourcesByIdentifier.size,
      // Refine's list may borrow a descendant's target; chrome needs the own target.
      menuTarget: item.to ?? null,
      ...(appRoot ? { appRoot: true } : {}),
      ...(item.app === true ? { app: true } : {}),
      ...(item.description ? { description: item.description } : {}),
      ...(item.group ? { group: item.group } : {}),
      ...(item.status ? { status: item.status } : {}),
      ...(item.tone ? { tone: item.tone } : {}),
      ...(item.badge !== undefined ? { badge: item.badge } : {}),
      ...(item.hidden ? { hidden: true } : {}),
      // Native breadcrumbs collapse repeated labels; chrome retains the full menu hierarchy.
      ...(breadcrumbParent ? { parent: menuRouteResourceIdentifier(breadcrumbParent.id) } : {}),
      ...(parent ? { menuParent: menuRouteResourceIdentifier(parent.id) } : {}),
    },
  });
}

/**
 * The capability names a composition requires, each with the menu items and
 * routes requiring it. A name that is not a capability identifier is a wiring error.
 */
function capabilityRequirements(
  routes: readonly BaseAddonRoute[],
  menuTree: MenuTree,
): ReadonlyMap<string, readonly string[]> {
  const requirements = new Map<string, string[]>();
  const add = (name: string | undefined, where: string): void => {
    if (name === undefined) return;
    if (!CAPABILITY_NAME.test(name)) throw new Error(`${where} requires "${name}", which is not a capability name.`);
    requirements.set(name, [...(requirements.get(name) ?? []), where]);
  };
  for (const item of menuTree.byId.values()) add(item.requires, `Menu item "${item.id}"`);
  for (const route of routes) add(route.requires, `Route "${route.name}"`);
  return requirements;
}

/**
 * Development findings for capabilities the composition requires but the
 * installation does not declare: nobody can hold them, so their pages never show.
 */
export function undeclaredCapabilityFindings(
  requirements: ReadonlyMap<string, readonly string[]>,
  declared: readonly string[],
): string[] {
  const known = new Set(declared);
  return [...requirements.keys()].filter((name) => !known.has(name)).sort().map((name) =>
    `${requirements.get(name)!.join(", ")} require capability "${name}", which the installation does not declare.`);
}

/** The capability a gated route lacks, and the route requiring it: itself or a route it nests under. */
interface RouteGate {
  capability: string;
  route: string;
}

/** Routes requiring a capability outside `capabilities`, with their route descendants in every layout. */
function gatedRoutes(routes: readonly BaseAddonRoute[], capabilities: ReadonlySet<string>): Map<string, RouteGate> {
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const gated = new Map<string, RouteGate>();
  for (const route of routes) {
    const gate = inheritedRouteFact(route, routesByName, (item) =>
      item.requires !== undefined && !capabilities.has(item.requires) ? { capability: item.requires, route: item.name } : undefined);
    if (gate) gated.set(route.name, gate);
  }
  return gated;
}

/**
 * The menu nodes an actor holding `capabilities` lacks, outermost first: a node
 * requiring another capability or targeting a gated page, with its subtree, and
 * a routeless node whose every destination left with them.
 */
function deniedMenuNodes(
  menuTree: MenuTree,
  capabilities: ReadonlySet<string>,
  gated: ReadonlyMap<string, RouteGate>,
): MenuNodeLeft[] {
  const denied: MenuNodeLeft[] = [];
  const visit = (item: ChromeMenuNode, under: string | undefined): { left: boolean; target: boolean } => {
    const gate = item.route !== undefined ? gated.get(item.route) : undefined;
    const reason = under !== undefined ? `sits under menu item "${under}"`
      : item.requires !== undefined && !capabilities.has(item.requires) ? `requires capability "${item.requires}"`
        : gate ? `targets a page that requires capability "${gate.capability}"` : undefined;
    const at = denied.length;
    const children = (item.children ?? []).map((child) => visit(child, reason !== undefined ? under ?? item.id : undefined));
    const target = Boolean(item.to) || children.some((child) => child.target);
    // A node that reached somewhere only through items that left has nowhere to go.
    const emptied = reason === undefined && !target && children.some((child) => child.left);
    if (reason === undefined && !emptied) return { left: false, target };
    denied.splice(at, 0, { id: item.id, ...(item.route ? { route: item.route } : {}), reason: reason ?? "has no item left" });
    return { left: true, target: false };
  };
  for (const root of menuTree.roots) visit(root, undefined);
  return denied;
}

/**
 * Routes made unavailable, each with its reason. A console route is unavailable
 * when every menu reference to it left: its targets and its `route.menu`
 * anchor, by composition removal or a capability the actor lacks. A surviving
 * reference keeps it available; routes no menu ever referenced, and routes
 * outside the console layout, stay available. Given the actor's `capabilities`,
 * a route requiring another one is unavailable in any layout. Route descendants
 * (`route.parent`) follow. An anchor naming no menu item at all is a wiring error.
 */
export function unavailableRoutes(
  routes: readonly BaseAddonRoute[],
  menuTree: MenuTree,
  removed: readonly MenuNodeLeft[],
  capabilities?: ReadonlySet<string>,
): Map<string, string> {
  const removedIds = new Set(removed.map((node) => node.id));
  const reasons = new Map(removed.map((node) => [node.id, node.reason ?? "was removed"]));
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const layoutOf = (route: BaseAddonRoute): string => {
    for (let current: BaseAddonRoute | undefined = route; current; current = current.parent ? routesByName.get(current.parent) : undefined) {
      if (current.layout) return current.layout;
    }
    return "console";
  };
  const unavailable = new Map<string, string>();
  const consider = (route: BaseAddonRoute, reason: string): void => {
    if (unavailable.has(route.name) || layoutOf(route) !== "console") return;
    if (menuTree.itemsForRoute(route.name).length === 0) unavailable.set(route.name, reason);
  };
  for (const route of routes) {
    if (route.menu && !menuTree.byId.has(route.menu) && !removedIds.has(route.menu)) {
      throw new Error(`Route "${route.name}" references unknown menu item "${route.menu}".`);
    }
  }
  const gates = capabilities ? gatedRoutes(routes, capabilities) : new Map<string, RouteGate>();
  for (const [name, gate] of gates) {
    unavailable.set(name, gate.route === name
      ? `it requires capability "${gate.capability}"`
      : `it nests under route "${gate.route}", which requires capability "${gate.capability}"`);
  }
  for (const node of removed) {
    const route = node.route ? routesByName.get(node.route) : undefined;
    if (route) consider(route, `menu item "${node.id}" ${reasons.get(node.id)}`);
  }
  for (const route of routes) {
    if (route.menu && removedIds.has(route.menu)) consider(route, `its menu anchor "${route.menu}" ${reasons.get(route.menu)}`);
  }
  for (let grew = true; grew;) {
    grew = false;
    for (const route of routes) {
      if (route.parent && unavailable.has(route.parent) && !unavailable.has(route.name) && layoutOf(route) === "console") {
        unavailable.set(route.name, `its parent route "${route.parent}" is unavailable`);
        grew = true;
      }
    }
  }
  return unavailable;
}

export function menuNodeForRoute(
  route: BaseAddonRoute,
  menuTree: MenuTree,
): ChromeMenuNode | undefined {
  // An anchor composition removed falls back to the route's surviving references;
  // `unavailableRoutes` already refused anchors that name no menu item.
  const anchor = route.menu ? menuTree.byId.get(route.menu) : undefined;
  // An item with params names one destination of a parameterized route, so only
  // route-level refs can contest the declared chrome owner or stand in for a removed one.
  const refs = menuTree.itemsForRoute(route.name).filter((item) => item.params === undefined);
  if (route.menu && anchor) {
    if (refs.length > 0 && !refs.some((item) => item.id === anchor.id)) {
      throw new Error(
        `Route "${route.name}" sets menu "${route.menu}", but that item does not reference the route.`,
      );
    }
    return anchor;
  }
  return refs.length === 1 ? refs[0] : undefined;
}

function menuRouteShowPath(
  item: ChromeMenuNode,
  routesByName: ReadonlyMap<string, BaseAddonRoute>,
  childrenByParentName: ReadonlyMap<string, readonly BaseAddonRoute[]>,
): string | undefined {
  const route = item.route ? routesByName.get(item.route) : undefined;
  if (!route) return undefined;
  const child = childrenByParentName
    .get(route.name)
    ?.find((candidate) => routeChildHasTrailingParam(candidate, route));
  return child ? fullRoutePath(child, route) : undefined;
}

export function menuRouteResourceIdentifier(menuId: string): string {
  return `menu:${menuId}`;
}

function routeLabel(route: BaseAddonRoute): RefineResourceMetadata {
  return typeof route.title === "string" ? { label: route.title } : {};
}

function routeIcon(route: BaseAddonRoute): RefineResourceMetadata {
  return route.icon ? { icon: route.icon } : {};
}

function breadcrumbTrailFromMenuTrail(
  trail: readonly ChromeMenuNode[],
): readonly ChromeMenuNode[] {
  const items: ChromeMenuNode[] = [];
  for (const item of trail) {
    const previous = items.at(-1);
    if (
      previous &&
      previous.displayLabel === item.displayLabel &&
      previous.target === item.target
    ) {
      items[items.length - 1] = item;
      continue;
    }
    items.push(item);
  }
  return items;
}

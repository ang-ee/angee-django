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
  type ChromeMenuNode,
  type MenuMatch,
} from "@angee/ui/chrome/menu-tree";
import { recordMatchValues, type RuntimeResourceRoutes } from "@angee/ui/runtime";

import type { BaseAddonRoute } from "./define-base-addon";
import { commonBase } from "./layers";
import { absentMenuNodes } from "./presence";
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

/**
 * The canonical claim per resource among the roots' claimants: the one route, or
 * those of the addon every other claimant depends on, as a dependent's page of a
 * model is its own app's. Addons that do not depend on one another cannot both
 * claim a resource.
 */
function canonicalClaims(
  claimants: ReadonlyMap<string, readonly BaseAddonRoute[]>,
  owners: Readonly<Record<string, string>>,
  ancestors: ReadonlyMap<string, ReadonlySet<string>>,
): BaseAddonRoute[] {
  return [...claimants].flatMap(([resource, routes]) => {
    if (routes.length === 1) return routes;
    const ownerOf = (route: BaseAddonRoute): string => owners[route.name] ?? route.name;
    const base = commonBase(routes.map(ownerOf), ancestors);
    if (base === undefined) {
      const named = routes.map((route) => `"${route.name}" of "${ownerOf(route)}"`).join(", ");
      throw new Error(`Routes ${named} claim resource "${resource}" from addons that do not depend on one another; one must depend on the other.`);
    }
    return routes.filter((route) => ownerOf(route) === base);
  });
}

/** One projection for app resource claims, navigation membership and route availability. */
export class AppRouteProjection {
  readonly navigationTree: MenuTree;
  readonly canonical: Readonly<Record<string, RuntimeResourceRoutes>>;
  /** The root pages outside a selected app's rail sit in: its home's root, else its first. */
  readonly homeApp: string | undefined;
  private readonly roots = new Map<string, string | undefined>();
  private readonly claims = new Map<string, Map<string, RuntimeResourceRoutes[]>>();
  /** Record match claims per resource, in every app: a matching row opens there. */
  private readonly recordDestinations = new Map<string, NonNullable<RuntimeResourceRoutes["recordDestinations"]>>();
  private readonly routesByName: ReadonlyMap<string, BaseAddonRoute>;
  private readonly presentNavigation = new Map<string, MenuTree>();

  /** Console routes removed menu nodes made unavailable, with the reason; they redirect home. */
  readonly unavailable: ReadonlyMap<string, string>;

  /**
   * `menuTree` is the logical tree (route ownership, trails); `selection` is the
   * selected app's rail and home route, without which every root shows;
   * `navigation` is what the chrome shows (defaults to the logical tree);
   * `removed` lists the menu nodes composition removed, which decides route
   * availability. Every root claims its own resource routes, whatever the
   * selection; `owners` (each route's addon) and `ancestors` (each addon's
   * dependencies) decide which claim is canonical, so links are the same on
   * every host.
   */
  constructor(
    readonly routes: readonly BaseAddonRoute[],
    readonly menuTree: MenuTree,
    readonly selection?: { rail: readonly string[]; home?: string },
    options: {
      navigation?: MenuTree;
      removed?: readonly { id: string; route?: string }[];
      owners?: Readonly<Record<string, string>>;
      ancestors?: ReadonlyMap<string, ReadonlySet<string>>;
    } = {},
  ) {
    const navigation = options.navigation ?? menuTree;
    this.navigationTree = selection === undefined ? navigation.withSettingsPlace() : navigation.confineTo(selection.rail);
    this.unavailable = unavailableRoutes(routes, menuTree, options.removed ?? []);
    this.routesByName = new Map(routes.map((route) => [route.name, route]));
    const home = selection?.home !== undefined ? this.routesByName.get(selection.home) : undefined;
    this.homeApp = selection && ((home && this.rootFor(home)) ?? selection.rail[0]);
    const canonical: BaseAddonRoute[] = [];
    const claimants = new Map<string, BaseAddonRoute[]>();
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
      const claimOf = (model: string) => resourceRouteIndex([
        { ...route, resource: model },
        ...routes.filter((child) => child.parent === route.name),
      ])[model]!;
      const match = route.recordMatch;
      if (match && resource) {
        if (route.path.includes("$")) throw new Error(`Route "${route.name}" declares recordMatch on a parameterized path.`);
        const { record } = claimOf(resource);
        if (!record) throw new Error(`Route "${route.name}" declares recordMatch without a record child.`);
        const destinations = this.recordDestinations.get(resource) ?? [];
        const values = recordMatchValues(match);
        const taken = destinations.find((item) => item.match.field === match.field
          && recordMatchValues(item.match).some((value) => values.includes(value)));
        if (taken) {
          const shared = recordMatchValues(taken.match).find((value) => values.includes(value));
          throw new Error(`Resource "${resource}" has duplicate record match "${match.field}=${shared}".`);
        }
        this.recordDestinations.set(resource, [...destinations, { record, match }]);
      }
      if (!resource || !root) {
        canonical.push(route);
        continue;
      }
      if (route.path.includes("$")) {
        if (route.resource) resourceRouteIndex([route]); // Same collection contract in every scope.
        continue;
      }
      const claim = claimOf(resource);
      const resources = this.claims.get(root) ?? new Map<string, RuntimeResourceRoutes[]>();
      resources.set(resource, [...(resources.get(resource) ?? []), claim]);
      this.claims.set(root, resources);
      // A borrowed model (`recordModel`, as a mount's alias) never claims canonically.
      if (route.resource) claimants.set(route.resource, [...(claimants.get(route.resource) ?? []), route]);
    }
    this.canonical = resourceRouteIndex([
      ...canonical,
      ...canonicalClaims(claimants, options.owners ?? {}, options.ancestors ?? new Map()),
    ]);
  }

  rootFor(route: BaseAddonRoute, visited = new Set<string>()): string | undefined {
    if (this.roots.has(route.name)) return this.roots.get(route.name);
    if (visited.has(route.name)) throw new Error(`Route "${route.name}" creates a parent cycle.`);
    visited.add(route.name);
    // Record children inherit their collection owner; a route several roots
    // reference without a `route.menu` anchor has no owner.
    const parent = route.parent ? this.routesByName.get(route.parent) : undefined;
    const selected = route.menu ? menuNodeForRoute(route, this.menuTree) : undefined;
    const refs = selected ? [selected] : this.menuTree.itemsForRoute(route.name);
    const roots = new Set(refs.map((item) => this.menuTree.trailFor(item.id)[0]?.id));
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

  activeMenu(pathname: string, routeName?: string, search?: string, navigation = this.navigationTree): MenuMatch | undefined {
    return navigation.match(pathname, search, false, this.menuAnchor(routeName)?.id);
  }

  /**
   * The navigation a session holding `permitted` sees: a menu node whose
   * `requires` it lacks leaves the rail, menus and palette with its subtree.
   * Presence only: routes, trails, app scope and admission do not change.
   * Without refs (`undefined`) the navigation stands as declared.
   */
  navigationFor(permitted: readonly string[] | undefined): MenuTree {
    if (permitted === undefined) return this.navigationTree;
    const key = [...new Set(permitted)].sort().join("\n");
    let navigation = this.presentNavigation.get(key);
    if (!navigation) {
      navigation = this.navigationTree.without(absentMenuNodes(this.menuTree, permitted));
      this.presentNavigation.set(key, navigation);
    }
    return navigation;
  }

  /**
   * The logical root a page sits in; lifting a node into Settings does not change
   * it. A page outside a selected app's rail sits in the app's home root.
   */
  activeApp(pathname: string, routeName?: string, search?: string): string | undefined {
    const root = this.menuTree.match(pathname, search, false, this.menuAnchor(routeName)?.id)?.trail[0]?.id;
    return this.inRail(root) ? root : this.homeApp;
  }

  /**
   * The apps a container `when: { app }` matches on this path, outermost first:
   * every app on its menu trail. A page outside a selected app's rail sits in the
   * app's home root alone.
   */
  appTrail(pathname: string, routeName?: string, search?: string): readonly string[] {
    const trail = this.menuTree.appTrail(pathname, search, this.menuAnchor(routeName)?.id).map((item) => item.id);
    return this.inRail(trail[0]) ? trail : [this.homeApp!];
  }

  /** Whether a root shows on the rail: any root when no app is selected. */
  private inRail(root: string | undefined): boolean {
    return this.selection === undefined || (root !== undefined && this.selection.rail.includes(root));
  }

  /** Collection defaults are inherited by its record children. */
  defaultResourceView(routeName?: string): string | undefined {
    const route = routeName ? this.routesByName.get(routeName) : undefined;
    return route ? inheritedRouteFact(route, this.routesByName, (item) => item.defaultResourceView) : undefined;
  }

  /**
   * The resource routes on a page of `app`. A record opens, in order: at the
   * record match claim its row matches, in every app; at the app's own claim;
   * at the canonical route.
   */
  resourceRoutes(app?: string, activeRoute?: string): Readonly<Record<string, RuntimeResourceRoutes>> {
    const result = { ...this.canonical };
    const route = activeRoute ? this.routesByName.get(activeRoute) : undefined;
    const owned = this.claims.get(app ?? "");
    for (const [resource, claims] of owned ?? []) {
      const selected = claims.find((claim) => route && inheritedRouteFact(route, this.routesByName,
        (ancestor) => ancestor.name === claim.collection ? true : undefined)) ?? claims[0]!;
      const record = selected.record ?? claims.find((claim) => claim.record)?.record ?? this.canonical[resource]?.record;
      result[resource] = { ...selected, ...(record ? { record } : {}) };
    }
    for (const [resource, destinations] of this.recordDestinations) {
      const selected = result[resource];
      if (!selected) continue;
      // A row no match claims opens at the app's own claim, never another match's.
      const fallback = owned?.get(resource)?.find((claim) => claim.record
        && !this.routesByName.get(claim.collection)?.recordMatch)?.record ?? this.canonical[resource]?.record;
      result[resource] = {
        ...selected,
        recordDestinations: destinations,
        ...(fallback ? { recordFallback: fallback } : {}),
      };
    }
    return result;
  }
}

function addMenuRouteResource(
  resourcesByIdentifier: Map<string, ResourceProps>,
  item: ChromeMenuNode,
  parent: ChromeMenuNode | undefined,
  showPath: string | undefined,
  breadcrumbParent: ChromeMenuNode | undefined,
): void {
  const target = item.target;
  if (!target || target === "#") return;
  const identifier = menuRouteResourceIdentifier(item.id);
  if (resourcesByIdentifier.has(identifier)) return;
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
 * Console routes made unavailable by removed menu nodes, each with its reason. A
 * route is unavailable when every menu reference to it was removed: its targets
 * and its `route.menu` anchor. A surviving reference keeps it available; route
 * descendants (`route.parent`) follow; routes no menu ever referenced, and
 * routes outside the console layout, stay available. An anchor naming no menu
 * item at all is a wiring error.
 */
export function unavailableRoutes(
  routes: readonly BaseAddonRoute[],
  menuTree: MenuTree,
  removed: readonly { id: string; route?: string }[],
): Map<string, string> {
  const removedIds = new Set(removed.map((node) => node.id));
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
  for (const node of removed) {
    const route = node.route ? routesByName.get(node.route) : undefined;
    if (route) consider(route, `menu item "${node.id}" was removed`);
  }
  for (const route of routes) {
    if (route.menu && removedIds.has(route.menu)) consider(route, `its menu anchor "${route.menu}" was removed`);
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

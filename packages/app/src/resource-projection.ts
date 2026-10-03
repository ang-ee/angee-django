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
      addMenuRouteResource(
        resourcesByIdentifier,
        item,
        menuTrail[index - 1],
        appRootIds.has(item.id),
        menuRouteShowPath(item, routesByName, childrenByParentName),
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

/** One projection for app resource claims, navigation membership and admission. */
export class AppRouteProjection {
  readonly navigationTree: MenuTree;
  readonly canonical: Readonly<Record<string, RuntimeResourceRoutes>>;
  private readonly roots = new Map<string, string | undefined>();
  private readonly claims = new Map<string, Map<string, RuntimeResourceRoutes[]>>();
  private readonly recordDestinations = new Map<string, Map<string, NonNullable<RuntimeResourceRoutes["recordDestinations"]>>>();
  private readonly routesByName: ReadonlyMap<string, BaseAddonRoute>;

  /** Console routes removed menu nodes made unavailable, with the reason; they redirect home. */
  readonly unavailable: ReadonlyMap<string, string>;

  /**
   * `menuTree` is the logical tree (route ownership, trails); `navigation` is what
   * the chrome shows (defaults to the logical tree); `removed` lists the menu
   * nodes composition removed, which decides route availability.
   */
  constructor(
    readonly routes: readonly BaseAddonRoute[],
    readonly menuTree: MenuTree,
    readonly confineTo?: string,
    options: { navigation?: MenuTree; removed?: readonly { id: string; route?: string }[] } = {},
  ) {
    const navigation = options.navigation ?? menuTree;
    this.navigationTree = confineTo === undefined ? navigation.withSettingsPlace() : navigation.confineTo(confineTo);
    this.unavailable = unavailableRoutes(routes, menuTree, options.removed ?? []);
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

  activeApp(pathname: string): string | undefined {
    return this.confineTo ?? this.menuTree.activeAppRoot(pathname)?.id;
  }

  /**
   * The apps a container `when: { app }` matches on this path, outermost first:
   * every app on its menu trail. Under a confinement only the root's own count;
   * a page another root owns (a Settings link) sits in the root alone.
   */
  appTrail(pathname: string): readonly string[] {
    const trail = this.menuTree.appTrail(pathname).map((item) => item.id);
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
      ...(parent ? { parent: menuRouteResourceIdentifier(parent.id) } : {}),
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
  if (route.menu && anchor) {
    const selected = anchor;
    const refs = menuTree.itemsForRoute(route.name);
    if (refs.length > 0 && !refs.some((item) => item.id === selected.id)) {
      throw new Error(
        `Route "${route.name}" sets menu "${route.menu}", but that item does not reference the route.`,
      );
    }
    return selected;
  }
  const refs = menuTree.itemsForRoute(route.name);
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

function menuRouteResourceIdentifier(menuId: string): string {
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

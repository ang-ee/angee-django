import {
  dataResourcesFromAngeeSchemaMetadata,
  refineResourcesFromDataResources,
  refineRoutePathForTanStack,
  type AngeeSchemaMetadata,
  type RefineResourceMetadata,
} from "@angee/metadata";
import type { ResourceProps } from "@refinedev/core";
import {
  MenuTree,
  type ChromeMenuNode,
} from "@angee/ui/chrome/menu-tree";

import type { BaseAddonRoute } from "./define-base-addon";
import {
  childRoutesByParentName,
  fullRoutePath,
  routeChildHasTrailingParam,
} from "./route-paths";

interface SchemaWithMetadata {
  metadata?: AngeeSchemaMetadata;
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
): RefineRouteResourceProjection {
  const resourcesByIdentifier = new Map<string, ResourceProps>();
  const metadataByResource: Record<string, RefineResourceMetadata> = {};
  const appRootIds = new Set(menuTree.roots.map((item) => item.id));
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const childrenByParentName = childRoutesByParentName(routes);

  for (const node of menuTree.byId.values()) {
    const menuTrail = menuTree.trailFor(node.id);
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
    if (!route.resource) continue;
    const selected = menuNodeForRouteResource(route, menuTree);
    const trail = selected
      ? breadcrumbTrailFromMenuTrail(menuTree.trailFor(selected.id))
      : [];
    const leaf = trail.at(-1);
    const parent = trail.length > 1 ? trail[trail.length - 2] : undefined;
    metadataByResource[route.resource] = {
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
 * Index each resource to the base path of the collection route that lists it (the
 * `resource`-tagged route), for relation-follow navigation. A resource may be
 * claimed by only one route; a second claim is a build-time error, matching the
 * registry collision discipline elsewhere.
 */
export function resourceRouteIndex(
  routes: readonly BaseAddonRoute[],
): Record<string, string> {
  const byResource: Record<string, string> = {};
  for (const route of routes) {
    if (!route.resource) continue;
    if (Object.prototype.hasOwnProperty.call(byResource, route.resource)) {
      throw new Error(
        `Route "${route.name}" claims resource "${route.resource}" already claimed by another route.`,
      );
    }
    byResource[route.resource] = route.path;
  }
  return byResource;
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
      ...(appRoot ? { appRoot: true } : {}),
      ...(item.description ? { description: item.description } : {}),
      ...(item.group ? { group: item.group } : {}),
      ...(item.sidebar !== undefined ? { sidebar: item.sidebar } : {}),
      ...(item.status ? { status: item.status } : {}),
      ...(item.tone ? { tone: item.tone } : {}),
      ...(item.badge !== undefined ? { badge: item.badge } : {}),
      ...(parent ? { parent: menuRouteResourceIdentifier(parent.id) } : {}),
    },
  });
}

function menuNodeForRouteResource(
  route: BaseAddonRoute,
  menuTree: MenuTree,
): ChromeMenuNode | undefined {
  if (route.menu) {
    const selected = menuTree.byId.get(route.menu);
    if (!selected) {
      throw new Error(
        `Route "${route.name}" references unknown menu item "${route.menu}".`,
      );
    }
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

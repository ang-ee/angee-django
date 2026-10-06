import type { BaseAddonRoute } from "./define-base-addon";
import type { MenuMount } from "./menus";
import {
  childRoutesByParentName,
  fullRoutePath,
  inheritedRouteFact,
  routeChildHasTrailingParam,
  routePathUnderParent,
} from "./route-paths";

export interface MountedRoutes {
  /** The resolved routes followed by every mount's alias routes. */
  routes: readonly BaseAddonRoute[];
  /** One family per mount: the mounted route's names mapped to its alias's (`iam.users` → `x.people`). */
  families: readonly ReadonlyMap<string, string>[];
}

/**
 * Borrow pages: each mount becomes an alias route named after its menu node, at
 * the mount's path, anchored to that node, reusing the mounted route's page
 * (`component`, `indexComponent`), layout and model (as `recordModel`, so the
 * canonical resource claim stays with its owner). The mount's
 * `defaultResourceView` and `recordMatch` go onto the alias. A record child
 * (the child ending in a param) gets an alias too, `<id>.record`, with the
 * owner's detail page. Takes and returns resolved routes.
 */
export function mountRoutes(
  routes: readonly BaseAddonRoute[],
  mounts: readonly MenuMount[],
): MountedRoutes {
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const childrenByParentName = childRoutesByParentName(routes);
  const names = new Set(routesByName.keys());
  const claim = (mount: MenuMount, name: string): string => {
    if (names.has(name)) throw new Error(`Menu item "${mount.id}" mounts "${mount.route}" as route "${name}", which another route already names.`);
    names.add(name);
    return name;
  };
  const aliases: BaseAddonRoute[] = [];
  const families = mounts.map((mount) => {
    const source = routesByName.get(mount.route);
    if (!source) throw new Error(`Menu item "${mount.id}" mounts unknown route "${mount.route}".`);
    if (source.path.includes("$")) {
      throw new Error(`Menu item "${mount.id}" mounts parameterized route "${mount.route}"; mount a collection or page route.`);
    }
    const records = (childrenByParentName.get(source.name) ?? []).filter((child) => routeChildHasTrailingParam(child, source));
    if (records.length > 1) throw new Error(`Route "${source.name}" has multiple record routes, which a mount cannot choose between.`);
    const layout = inheritedRouteFact(source, routesByName, (route) => route.layout);
    const model = inheritedRouteFact(source, routesByName, (route) => route.recordModel ?? route.resource);
    const defaultResourceView = mount.defaultResourceView ?? inheritedRouteFact(source, routesByName, (route) => route.defaultResourceView);
    const collection: BaseAddonRoute = {
      name: claim(mount, mount.id),
      path: mount.path,
      menu: mount.id,
      ...(layout ? { layout } : {}),
      ...(source.component ? { component: source.component } : {}),
      ...(source.indexComponent ? { indexComponent: source.indexComponent } : {}),
      ...(model ? { recordModel: model } : {}),
      ...(defaultResourceView ? { defaultResourceView } : {}),
      ...(mount.recordMatch ? { recordMatch: mount.recordMatch } : {}),
    };
    aliases.push(collection);
    const family = new Map([[source.name, collection.name]]);
    const record = records[0];
    if (record) {
      const name = claim(mount, `${mount.id}.record`);
      const path = fullRoutePath({ name, path: routePathUnderParent(record, source) }, collection);
      aliases.push({
        name,
        path,
        parent: collection.name,
        ...(record.component ? { component: record.component } : {}),
        ...(record.indexComponent ? { indexComponent: record.indexComponent } : {}),
      });
      family.set(record.name, name);
    }
    return family;
  });
  return { routes: [...routes, ...aliases], families };
}

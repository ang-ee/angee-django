import type { BaseAddonRoute } from "./define-base-addon";
import { routeParameterName } from "@angee/ui/runtime";

/** Resolve declaration ancestry once before href, menu and runtime projections. */
export function resolveRoutePaths(routes: readonly BaseAddonRoute[]): readonly BaseAddonRoute[] {
  const byName = new Map(routes.map((route) => [route.name, route]));
  const resolved = new Map<string, BaseAddonRoute>();
  const visiting = new Set<string>();
  const resolve = (route: BaseAddonRoute): BaseAddonRoute => {
    const cached = resolved.get(route.name);
    if (cached) return cached;
    if (visiting.has(route.name)) throw new Error(`Route "${route.name}" creates a parent cycle.`);
    visiting.add(route.name);
    const parent = route.parent ? byName.get(route.parent) : undefined;
    if (route.parent && !parent) throw new Error(`Route "${route.name}" references unknown parent route "${route.parent}".`);
    const result = { ...route, path: fullRoutePath(route, parent ? resolve(parent) : undefined) };
    resolved.set(route.name, result);
    visiting.delete(route.name);
    return result;
  };
  return routes.map(resolve);
}

/** Return the nearest declared route fact, including explicit empty values. */
export function inheritedRouteFact<T>(
  route: BaseAddonRoute,
  routesByName: ReadonlyMap<string, BaseAddonRoute>,
  fact: (route: BaseAddonRoute) => T | undefined,
): T | undefined {
  const value = fact(route);
  if (value !== undefined) return value;
  const parent = route.parent ? routesByName.get(route.parent) : undefined;
  return parent ? inheritedRouteFact(parent, routesByName, fact) : undefined;
}

export function routeChildHasTrailingParam(
  route: BaseAddonRoute,
  parent: BaseAddonRoute,
): boolean {
  const path = routePathUnderParent(route, parent);
  return Boolean(trailingRouteParamName(path));
}

export function fullRoutePath(
  route: BaseAddonRoute,
  parent: BaseAddonRoute | undefined,
): string {
  if (!parent) return normalizeRoutePath(route.path);
  const childPath = routePathUnderParent(route, parent);
  const parentPath = normalizeRoutePath(parent.path);
  if (!childPath) return parentPath;
  if (parentPath === "/") return normalizeRoutePath(`/${childPath}`);
  return normalizeRoutePath(`${parentPath}/${childPath}`);
}

export function routePathUnderParent(
  route: BaseAddonRoute,
  parent: BaseAddonRoute | undefined,
): string {
  if (!parent) return route.path;
  const parentPath = normalizeRoutePath(parent.path);
  const childPath = normalizeRoutePath(route.path);
  if (parentPath === childPath) return "";
  if (parentPath === "/") return childPath.slice(1);
  if (childPath.startsWith(`${parentPath}/`)) {
    return childPath.slice(parentPath.length + 1);
  }
  return childPath.slice(1);
}

export function normalizeRoutePath(path: string): string {
  if (path === "/") return "/";
  const withLeadingSlash = path.startsWith("/") ? path : `/${path}`;
  return withLeadingSlash.replace(/\/+$/, "");
}

export function trailingRouteParamName(path: string): string | undefined {
  const segment = normalizeRoutePath(path).split("/").at(-1);
  return segment ? routeParameterName(segment) : undefined;
}

export function childRoutesByParentName(
  routes: readonly BaseAddonRoute[],
): ReadonlyMap<string, readonly BaseAddonRoute[]> {
  const children = new Map<string, BaseAddonRoute[]>();
  for (const route of routes) {
    if (!route.parent) continue;
    children.set(route.parent, [...(children.get(route.parent) ?? []), route]);
  }
  return children;
}

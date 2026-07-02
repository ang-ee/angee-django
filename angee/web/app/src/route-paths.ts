import type { BaseAddonRoute } from "./define-base-addon";

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
  return segment?.startsWith("$") ? segment.slice(1) || undefined : undefined;
}

export function resolvePath(
  nameOrPath: string | undefined,
  pathByName: ReadonlyMap<string, string>,
): string | undefined {
  if (!nameOrPath) return undefined;
  return pathByName.get(nameOrPath) ?? nameOrPath;
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

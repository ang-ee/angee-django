import * as React from "react";
import {
  Link as TanStackLink,
  useLocation,
  useNavigate,
} from "@tanstack/react-router";
import {
  ResourceContext,
  matchResourceFromRoute,
  type BaseKey,
  type GoConfig,
  type RouterProvider,
} from "@refinedev/core";

export const tanStackRouterProvider: RouterProvider = {
  go: () => {
    const navigate = useNavigate();
    return (config) => {
      if (config.type === "path") return urlFromGoConfig(config);
      void navigate({
        to: config.to || ".",
        search: config.query as never,
        hash: config.hash?.replace(/^#/, ""),
        replace: config.type === "replace",
      } as never);
    };
  },
  back: () => () => {
    if (typeof history !== "undefined") history.back();
  },
  parse: () => {
    const location = useLocation();
    const { resources } = React.useContext(ResourceContext);
    return () => ({
      ...parsedResourceRoute(location.pathname, resources),
      pathname: location.pathname,
      params: {
        ...(location.search as Record<string, unknown>),
        ...parsedRouteParams(location.pathname, resources),
      },
    });
  },
  Link: ({ to, children, ...props }) =>
    React.createElement(
      TanStackLink as React.ComponentType<React.PropsWithChildren<{ to: string }>>,
      { ...props, to },
      children,
    ),
};

function parsedResourceRoute(
  pathname: string,
  resources: React.ContextType<typeof ResourceContext>["resources"],
) {
  const match = matchResourceFromRoute(pathname, resources);
  if (!match.found) return {};
  const routeParams = match.matchedRoute
    ? paramsFromMatchedRoute(pathname, match.matchedRoute)
    : {};
  return {
    ...(match.resource ? { resource: match.resource } : {}),
    ...(match.action ? { action: match.action } : {}),
    ...(routeId(routeParams) !== undefined ? { id: routeId(routeParams) } : {}),
  };
}

function parsedRouteParams(
  pathname: string,
  resources: React.ContextType<typeof ResourceContext>["resources"],
): Record<string, string> {
  const match = matchResourceFromRoute(pathname, resources);
  return match.matchedRoute
    ? paramsFromMatchedRoute(pathname, match.matchedRoute)
    : {};
}

function paramsFromMatchedRoute(
  pathname: string,
  matchedRoute: string,
): Record<string, string> {
  const pathSegments = routeSegments(pathname);
  const routeSegmentsList = routeSegments(matchedRoute);
  const params: Record<string, string> = {};
  routeSegmentsList.forEach((segment, index) => {
    if (!segment.startsWith(":")) return;
    const key = segment.slice(1);
    const value = pathSegments[index];
    if (key && value !== undefined) params[key] = decodeURIComponent(value);
  });
  return params;
}

function routeId(params: Record<string, string>): BaseKey | undefined {
  if (params.id) return params.id;
  const first = Object.values(params)[0];
  return first === "" ? undefined : first;
}

function routeSegments(route: string): readonly string[] {
  return route
    .replace(/^\/+|\/+$/g, "")
    .split("/")
    .filter(Boolean);
}

export function urlFromGoConfig(config: GoConfig): string {
  const path = config.to ?? "";
  const query = queryString(config.query);
  const hash = config.hash
    ? `#${config.hash.replace(/^#/, "")}`
    : "";
  return `${path}${query}${hash}`;
}

function queryString(query: Record<string, unknown> | undefined): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value == null || value === "") continue;
    params.set(key, String(value));
  }
  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

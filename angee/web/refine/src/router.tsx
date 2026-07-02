import * as React from "react";
import {
  Link as TanStackLink,
  useNavigate,
  useRouter,
  useRouterState,
} from "@tanstack/react-router";
import {
  ResourceContext,
  matchResourceFromRoute,
  type BaseKey,
  type RouterProvider,
} from "@refinedev/core";

export const tanStackRouterProvider: RouterProvider = {
  go: () => {
    const navigate = useNavigate();
    const router = useRouter();
    return (config) => {
      if (config.type === "path") {
        const location = router.buildLocation({
          to: config.to || ".",
          search: config.query as never,
          hash: config.hash?.replace(/^#/, ""),
        } as never) as {
          hash?: string;
          href?: string;
          pathname: string;
          searchStr?: string;
        };
        return location.href
          ?? `${location.pathname}${location.searchStr ?? ""}${location.hash ?? ""}`;
      }
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
    const location = useRouterState({ select: (state) => state.location });
    const { resources } = React.useContext(ResourceContext);
    return React.useCallback(
      () =>
        parsedLocation(
          location.pathname,
          location.search as Record<string, unknown>,
          resources,
        ),
      [location.pathname, location.search, resources],
    );
  },
  Link: ({ to, children, ...props }) =>
    React.createElement(
      TanStackLink as React.ComponentType<React.PropsWithChildren<{ to: string }>>,
      { ...props, to },
      children,
    ),
};

type RefineResources = React.ContextType<typeof ResourceContext>["resources"];

function parsedLocation(
  pathname: string,
  search: Record<string, unknown>,
  resources: RefineResources,
) {
  const match = matchResourceFromRoute(pathname, resources);
  if (!match.found) {
    return {
      pathname,
      params: search,
    };
  }
  const routeParams = match.matchedRoute
    ? paramsFromMatchedRoute(pathname, match.matchedRoute)
    : {};
  const id = routeId(routeParams);
  return {
    ...(match.resource ? { resource: match.resource } : {}),
    ...(match.action ? { action: match.action } : {}),
    ...(id !== undefined ? { id } : {}),
    pathname,
    params: {
      ...search,
      ...routeParams,
    },
  };
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

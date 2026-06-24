import * as React from "react";
import {
  Link as TanStackLink,
  useLocation,
  useNavigate,
} from "@tanstack/react-router";
import type { GoConfig, RouterProvider } from "@refinedev/core";

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
    return () => ({
      pathname: location.pathname,
      params: location.search as Record<string, unknown>,
    });
  },
  Link: ({ to, children, ...props }) =>
    React.createElement(
      TanStackLink as React.ComponentType<React.PropsWithChildren<{ to: string }>>,
      { ...props, to },
      children,
    ),
};

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

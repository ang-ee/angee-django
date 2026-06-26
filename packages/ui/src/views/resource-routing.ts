import * as React from "react";
import type {
  Row,
} from "@angee/resources";
import {
  useMatches,
  useNavigate,
  useRouter,
  useRouterState,
  type AnyRoute,
  type AnyRouteMatch,
  } from "@tanstack/react-router";
import {
  rowPublicId,
} from "@angee/resources";

import type { ResourceRecordController } from "./ResourceList";

interface RoutedRecordControllerProps<TRow extends Row = Row> {
  children: (recordController: ResourceRecordController<TRow>) => React.ReactElement;
  newRecordId: string;
}

export function RoutedRecordController<TRow extends Row = Row>({
  children,
  newRecordId,
}: RoutedRecordControllerProps<TRow>): React.ReactElement {
  const fullPath = useMatches({ select: leafFullPath });
  const routeId = useMatches({ select: leafRouteId });
  const activeParamName = trailingRouteParamName(fullPath);
  const router = useRouter();
  const recordRouteFullPath = React.useMemo(
    () =>
      activeParamName
        ? fullPath
        : childRecordRouteFullPath(router.routesById[routeId]),
    [activeParamName, fullPath, routeId, router.routesById],
  );
  const recordParamName = recordRouteFullPath
    ? trailingRouteParamName(recordRouteFullPath)
    : undefined;
  const selectRecordId = React.useCallback(
    (matches: readonly AnyRouteMatch[]): string | undefined =>
      activeParamName ? matches.at(-1)!.params[activeParamName] : undefined,
    [activeParamName],
  );
  const recordId = useMatches({
    select: selectRecordId,
  });
  const basePath = React.useMemo(
    () =>
      recordRouteFullPath
        ? collectionBasePathFromRoute(recordRouteFullPath)
        : "",
    [recordRouteFullPath],
  );
  const navigate = useNavigate();
  const searchSuffix = useRouterState({
    select: (state) => searchSuffixFromHref(state.location.href),
  });
  const onSelect = React.useCallback(
    (id: string | null) => {
      void navigate({
        to: recordPath(basePath, id === null ? newRecordId : id),
        search: (prev: Record<string, unknown>) => prev,
      });
    },
    [basePath, navigate, newRecordId],
  );
  const onClose = React.useCallback(() => {
    void navigate({
      to: basePath,
      search: (prev: Record<string, unknown>) => prev,
    });
  }, [basePath, navigate]);
  const rowHref = React.useCallback(
    (row: TRow) => {
      const id = rowPublicId(row);
      return appendSearch(id ? recordPath(basePath, id) : basePath, searchSuffix);
    },
    [basePath, searchSuffix],
  );

  if (!recordParamName) {
    throw new Error(
      `ResourceList routed mode on route "${routeId}" needs a trailing $param child route.`,
    );
  }

  return children({
    recordId,
    onSelect,
    onClose,
    rowHref,
  });
}

function leafFullPath(matches: readonly AnyRouteMatch[]): string {
  return matches.at(-1)!.fullPath;
}

function leafRouteId(matches: readonly AnyRouteMatch[]): string {
  return matches.at(-1)!.routeId;
}

function childRecordRouteFullPath(route: AnyRoute): string | undefined {
  return route.children?.find((child: AnyRoute) =>
    Boolean(trailingRouteParamName(child.fullPath)),
  )?.fullPath;
}

function collectionBasePathFromRoute(fullPath: string): string {
  const normalized = normalizeRoutePath(fullPath);
  const segments = normalized.split("/");
  segments.pop();
  return segments.join("/") || "/";
}

function trailingRouteParamName(fullPath: string): string | undefined {
  const segment = normalizeRoutePath(fullPath).split("/").at(-1);
  return segment?.startsWith("$") ? segment.slice(1) || undefined : undefined;
}

function normalizeRoutePath(path: string): string {
  if (path === "/") return "/";
  return path.replace(/\/+$/, "") || "/";
}

/** Join a collection base path with a record id (shared by routed navigation and
 * the relation "follow" affordance, so id-encoding lives in one place). */
export function recordPath(basePath: string, id: string): string {
  if (basePath === "/") return `/${encodeURIComponent(id)}`;
  return `${basePath}/${encodeURIComponent(id)}`;
}

function appendSearch(path: string, searchSuffix: string): string {
  return searchSuffix ? `${path}${searchSuffix}` : path;
}

function searchSuffixFromHref(href: string): string {
  const queryStart = href.indexOf("?");
  if (queryStart < 0) return "";
  const hashStart = href.indexOf("#", queryStart);
  return hashStart < 0
    ? href.slice(queryStart)
    : href.slice(queryStart, hashStart);
}

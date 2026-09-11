import * as React from "react";
import {
  useMatches,
  useNavigate,
  useRouter,
  useRouterState,
  useParams,
  useSearch,
  type AnyRoute,
  type AnyRouteMatch,
} from "@tanstack/react-router";
import { rowPublicId, useModelMetadata, type Row } from "@angee/metadata";
import { useBreadcrumbCollectionLink } from "../../chrome/Breadcrumb";
import { useAppRuntime, routeParameterName } from "../../runtime";

import { RECORD_TAB_SEARCH_KEY, parseRecordNavigationScope, recordNavigationHref, recordNavigationSearch, recordTargetHref, recordTargetSearch, routeSearchParam } from "./record-navigation-context";
import type { ListViewNavigationScope } from "./resource-view-surface";

import type { ResourceRecordController } from "./ResourceList";

interface RoutedRecordControllerProps<TRow extends Row = Row> {
  children: (recordController: ResourceRecordController<TRow>) => React.ReactElement;
  newRecordId: string;
  resource: string;
}

export function RoutedRecordController<TRow extends Row = Row>({
  children,
  newRecordId,
  resource,
}: RoutedRecordControllerProps<TRow>): React.ReactElement {
  const { recordSearchKeys } = useAppRuntime();
  const clearedRecordSearch = React.useMemo(
    () => ({ tab: null, search: Object.fromEntries(recordSearchKeys.map((key) => [key, null])) }),
    [recordSearchKeys],
  );
  const dataResource = useModelMetadata(resource)?.resource;
  const search = useRouterState({ select: (state) => state.location.search as Record<string, unknown> });
  const recordTab = routeSearchParam(search, RECORD_TAB_SEARCH_KEY);
  const navigationScope = React.useMemo(() => parseRecordNavigationScope(search, dataResource), [dataResource, search]);
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
  useBreadcrumbCollectionLink(
    basePath,
    recordId === undefined ? null
      : recordNavigationHref(recordTargetHref(appendSearch(basePath, searchSuffix), clearedRecordSearch), dataResource, null),
  );
  const onSelect = React.useCallback(
    (id: string | null, scope?: ListViewNavigationScope) => {
      React.startTransition(() => {
        void navigate({
          to: recordPath(basePath, id === null ? newRecordId : id),
          search: (prev: Record<string, unknown>) => recordNavigationSearch(
            recordTargetSearch(prev, clearedRecordSearch),
            dataResource,
            id === null ? null : scope ?? navigationScope,
          ),
        });
      });
    },
    [basePath, clearedRecordSearch, dataResource, navigate, navigationScope, newRecordId],
  );
  const onClose = React.useCallback(() => {
    React.startTransition(() => {
      void navigate({
        to: basePath,
        search: (prev: Record<string, unknown>) => recordNavigationSearch(recordTargetSearch(prev, clearedRecordSearch), dataResource, null),
      });
    });
  }, [basePath, clearedRecordSearch, dataResource, navigate]);
  const onRecordTabChange = React.useCallback((tab: string) => {
    void navigate({
      to: ".",
      replace: true,
      search: (prev: Record<string, unknown>) => recordTargetSearch(prev, { tab }),
    });
  }, [navigate]);
  const rowHref = React.useCallback(
    (row: TRow, scope?: ListViewNavigationScope) => {
      const id = rowPublicId(row);
      return recordNavigationHref(recordTargetHref(appendSearch(id ? recordPath(basePath, id) : basePath, searchSuffix), clearedRecordSearch), dataResource, scope ?? navigationScope);
    },
    [basePath, clearedRecordSearch, dataResource, navigationScope, searchSuffix],
  );

  if (!recordParamName) {
    throw new Error(
      `ResourceList routed mode on route "${routeId}" needs a trailing $param child route.`,
    );
  }

  return children({
    recordId,
    navigationScope,
    onSelect,
    onClose,
    rowHref,
    recordTab,
    onRecordTabChange,
  });
}

export function useRouteRecordId(): string | undefined {
  const fullPath = useMatches({ select: leafFullPath });
  const activeParamName = trailingRouteParamName(fullPath);
  const selectRecordId = React.useCallback(
    (matches: readonly AnyRouteMatch[]): string | undefined =>
      activeParamName ? matches.at(-1)!.params[activeParamName] : undefined,
    [activeParamName],
  );
  return useMatches({ select: selectRecordId });
}

/** Read one named string parameter from the active route without asserting its route tree. */
export function useRouteParam(name: string): string | undefined {
  const params: Record<string, unknown> = useParams({ strict: false });
  const value = params[name];
  return typeof value === "string" ? value : undefined;
}

/** Read the app's flat search object through its router-owned runtime shape. */
export function useRouteSearch(): Readonly<Record<string, unknown>> {
  return useSearch({ strict: false });
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
  return segment ? routeParameterName(segment) : undefined;
}

function normalizeRoutePath(path: string): string {
  if (path === "/") return "/";
  return path.replace(/\/+$/, "") || "/";
}

/** Join the active routed collection base path with a record id. */
function recordPath(basePath: string, id: string): string {
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

// Router metadata readers. TanStack Router owns the active match branch and
// `staticData`; this module derives Angee chrome from that stable route data.

import {
  useMatches,
  type AnyRouteMatch,
} from "@tanstack/react-router";
import type { ReactElement, ReactNode } from "react";

/** One breadcrumb entry rendered by the shell trail. */
export interface BreadcrumbItem {
  label: ReactNode;
  to?: string;
}

/** Static shell chrome attached to a TanStack route. */
export interface RouteChromeStaticData {
  title?: ReactNode;
  icon?: string;
  breadcrumbs?: readonly BreadcrumbItem[];
}

/** Dynamic crumb factory attached to a route and evaluated from its match. */
export type RouteBreadcrumbFactory = (
  match: AnyRouteMatch,
) => ReactElement | string | null;

declare module "@tanstack/router-core" {
  interface StaticDataRouteOption {
    chrome?: RouteChromeStaticData;
    breadcrumb?: RouteBreadcrumbFactory;
  }
}

// The single fallback trail for route branches that do not contribute chrome.
const DEFAULT_BREADCRUMBS: readonly BreadcrumbItem[] = [{ label: "Console" }];

/** Read the deepest active route chrome from router staticData. */
export function useRouteChrome(): RouteChromeStaticData {
  return useMatches({ select: routeChromeFromMatches }) ?? {};
}

/** Read the active breadcrumb trail from router staticData. */
export function useRouteBreadcrumbItems(): readonly BreadcrumbItem[] {
  return breadcrumbItemsFromMatches(useMatches()) ?? DEFAULT_BREADCRUMBS;
}

/** Return the deepest match that carries route chrome. */
export function routeChromeFromMatches(
  matches: readonly AnyRouteMatch[],
): RouteChromeStaticData | undefined {
  for (let index = matches.length - 1; index >= 0; index -= 1) {
    const chrome = matches[index]?.staticData.chrome;
    if (chrome) return chrome;
  }
  return undefined;
}

/**
 * Build the route trail: deepest match with static crumbs wins; append deeper
 * crumb factories in match order, linking the static leaf when it becomes an
 * ancestor.
 */
export function breadcrumbItemsFromMatches(
  matches: readonly AnyRouteMatch[],
): readonly BreadcrumbItem[] | undefined {
  const chromeIndex = deepestBreadcrumbChromeIndex(matches);
  if (chromeIndex < 0) return undefined;

  const chromeMatch = matches[chromeIndex];
  const staticItems = [
    ...(chromeMatch?.staticData.chrome?.breadcrumbs ?? []),
  ];
  const dynamicItems = matches
    .slice(chromeIndex + 1)
    .flatMap((match) => breadcrumbItemFromMatch(match));

  if (dynamicItems.length > 0) {
    linkStaticLeaf(staticItems, chromeMatch?.pathname);
  }

  return [...staticItems, ...dynamicItems];
}

function deepestBreadcrumbChromeIndex(
  matches: readonly AnyRouteMatch[],
): number {
  for (let index = matches.length - 1; index >= 0; index -= 1) {
    if (matches[index]?.staticData.chrome?.breadcrumbs) return index;
  }
  return -1;
}

function breadcrumbItemFromMatch(match: AnyRouteMatch): BreadcrumbItem[] {
  const label = match.staticData.breadcrumb?.(match);
  if (label === null || label === "" || (label as unknown) === true) return [];
  return [{ label }];
}

function linkStaticLeaf(items: BreadcrumbItem[], pathname: string | undefined): void {
  const leaf = items.at(-1);
  if (!leaf || leaf.to || !pathname) return;
  items[items.length - 1] = { ...leaf, to: pathname };
}

import {
  snakeCaseIdentifier,
  type DataResourceMetadata,
} from "@angee/metadata";
import type { ChatterRoute } from "@angee/ui/runtime";

import type { BaseAddonRoute } from "./define-base-addon";
import {
  childRoutesByParentName,
  fullRoutePath,
  trailingRouteParamName,
} from "./route-paths";

interface ResourceFacts {
  resourceType?: string | null;
  canonicalLabel?: string | null;
}

export function chatterRouteIndex(
  routes: readonly BaseAddonRoute[],
  resources: readonly DataResourceMetadata[],
): readonly ChatterRoute[] {
  const resourceFactsByModel = resourceFactsByModelLabel(resources);
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const childrenByParentName = childRoutesByParentName(routes);
  return routes.map((route) => {
    const parent = route.parent ? routesByName.get(route.parent) : undefined;
    const path = fullRoutePath(route, parent);
    const recordParam = trailingRouteParamName(path);
    const modelLabel = inheritedRouteResource(route, routesByName);
    const canonicalLabel = modelLabel
      ? resourceFactsByModel[modelLabel]?.canonicalLabel
      : undefined;
    return {
      name: route.name,
      path,
      viewType: routeChatterViewType(
        route,
        routesByName,
        childrenByParentName,
        resourceFactsByModel,
      ),
      ...(modelLabel ? { modelLabel } : {}),
      ...(canonicalLabel ? { canonicalLabel } : {}),
      ...(recordParam ? { recordParam } : {}),
    };
  });
}

function routeChatterViewType(
  route: BaseAddonRoute,
  routesByName: ReadonlyMap<string, BaseAddonRoute>,
  childrenByParentName: ReadonlyMap<string, readonly BaseAddonRoute[]>,
  resourceFactsByModel: Readonly<Record<string, ResourceFacts>>,
): string {
  const resource = inheritedRouteResource(route, routesByName);
  if (resource) {
    const facts = resourceFactsByModel[resource];
    if (!facts) {
      throw new Error(
        `Chatter route "${route.name}" references unknown model "${resource}".`,
      );
    }
    return facts.resourceType ?? resourceTypeFromModelLabel(resource);
  }
  if (!trailingRouteParamName(route.path)) {
    const recordChild = childrenByParentName
      .get(route.name)
      ?.find((child) => trailingRouteParamName(child.path));
    if (recordChild) return routeNameViewType(recordChild.name);
  }
  return routeNameViewType(route.name);
}

function inheritedRouteResource(
  route: BaseAddonRoute,
  routesByName: ReadonlyMap<string, BaseAddonRoute>,
): string | undefined {
  if (route.resource) return route.resource;
  if (!route.parent) return undefined;
  const parent = routesByName.get(route.parent);
  return parent ? inheritedRouteResource(parent, routesByName) : undefined;
}

function resourceFactsByModelLabel(
  resources: readonly DataResourceMetadata[],
): Record<string, ResourceFacts> {
  const byModel: Record<string, ResourceFacts> = {};
  for (const resource of resources) {
    const facts = {
      resourceType: resource.resourceType,
      canonicalLabel: resource.canonicalLabel,
    };
    byModel[resource.modelLabel] ??= facts;
  }
  return byModel;
}

function resourceTypeFromModelLabel(modelLabel: string): string {
  const parts = modelLabel.split(".");
  const modelName = parts.pop();
  const appLabel = parts.join(".");
  if (!appLabel || !modelName) return routeNameViewType(modelLabel);
  return `${appLabel}/${snakeCaseIdentifier(modelName)}`;
}

function routeNameViewType(routeName: string): string {
  return routeName.split(".").map(snakeCaseIdentifier).join("/");
}

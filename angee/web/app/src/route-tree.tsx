import {
  ActiveGraphQLSchemaProvider,
  ModelMetadataProvider,
  type SchemaFieldMetadata,
} from "@angee/metadata";
import { ActiveDataProviderNameProvider } from "@angee/refine";
import type { AuthProvider as RefineAuthProvider } from "@refinedev/core";
import type { QueryClient } from "@tanstack/react-query";
import {
  type AnyRoute,
  Outlet,
  createRoute,
  redirect,
} from "@tanstack/react-router";
import type { ReactNode } from "react";

import type {
  BaseAddonRoute,
  RefineLayoutConfig,
} from "./define-base-addon";
import { identityQueryOptions } from "./providers/auth";
import { routePathUnderParent } from "./route-paths";

interface RouteSchemaConfig {
  fieldMetadata: SchemaFieldMetadata;
}

export function createLayoutRoutes({
  rootRoute,
  layoutNames,
  layouts,
  schemas,
  defaultSchema,
  authProvider,
  queryClient,
}: {
  rootRoute: AnyRoute;
  layoutNames: readonly string[];
  layouts: Record<string, RefineLayoutConfig>;
  schemas: Readonly<Record<string, RouteSchemaConfig>>;
  defaultSchema: string;
  authProvider: RefineAuthProvider;
  queryClient: QueryClient;
}): Map<string, AnyRoute> {
  const layoutRoutes = new Map<string, AnyRoute>();
  for (const layoutName of layoutNames) {
    const requireAuth = layoutRequiresAuth(layoutName, layouts);
    layoutRoutes.set(
      layoutName,
      createRoute({
        getParentRoute: () => rootRoute,
        id: refineLayoutRouteId(layoutName),
        ...(requireAuth
          ? { beforeLoad: authBeforeLoad(authProvider, queryClient) }
          : {}),
        component: () => (
          <RefineLayoutRoute
            layoutName={layoutName}
            layouts={layouts}
            schemas={schemas}
            defaultSchema={defaultSchema}
          />
        ),
      }),
    );
  }
  return layoutRoutes;
}

export function createAddonRouteNodes({
  routes,
  routesByName,
  layoutRoutes,
}: {
  routes: readonly BaseAddonRoute[];
  routesByName: ReadonlyMap<string, BaseAddonRoute>;
  layoutRoutes: ReadonlyMap<string, AnyRoute>;
}): void {
  const routeNodes = new Map<string, AnyRoute>();
  const childrenByParent = new Map<AnyRoute, Array<NamedRouteNode>>();

  const buildRoute = (route: BaseAddonRoute): AnyRoute => {
    const existing = routeNodes.get(route.name);
    if (existing) return existing;
    const parentManifestRoute = route.parent
      ? routesByName.get(route.parent)
      : undefined;
    if (route.parent && !parentManifestRoute) {
      throw new Error(
        `Route "${route.name}" references unknown parent route "${route.parent}".`,
      );
    }
    const parentNode = parentManifestRoute
      ? buildRoute(parentManifestRoute)
      : layoutRouteFor(route, layoutRoutes);
    const node = createAddonRouteNode(
      route,
      parentNode,
      parentManifestRoute,
    );
    routeNodes.set(route.name, node);
    const children = childrenByParent.get(parentNode) ?? [];
    children.push({ name: route.name, route: node });
    childrenByParent.set(parentNode, children);
    return node;
  };

  for (const route of [...routes].sort(compareRouteNames)) {
    buildRoute(route);
  }
  for (const [parent, children] of childrenByParent) {
    parent.addChildren(
      children
        .sort((a, b) => compareCodePoint(a.name, b.name))
        .map((child) => child.route),
    );
  }
}

export function layoutNamesForRoutes(
  layouts: Record<string, RefineLayoutConfig>,
): readonly string[] {
  return Object.keys(layouts).sort(compareCodePoint);
}

export function compareCodePoint(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function RefineLayoutRoute({
  layoutName,
  layouts,
  schemas,
  defaultSchema,
}: {
  layoutName: string;
  layouts: Record<string, RefineLayoutConfig>;
  schemas: Readonly<Record<string, RouteSchemaConfig>>;
  defaultSchema: string;
}): ReactNode {
  const layout = layouts[layoutName];
  const Chrome = layout?.chrome ?? PassthroughChrome;
  const schemaName = layout?.schema ?? defaultSchema;
  const schema = schemas[schemaName];
  if (!schema) {
    const known = Object.keys(schemas).join(", ") || "none";
    throw new Error(
      `No GraphQL schema config for layout "${layoutName}" schema ` +
        `"${schemaName}"; configured schemas: ${known}.`,
    );
  }
  const body = (
    <Chrome>
      <Outlet />
    </Chrome>
  );
  return (
    <ActiveGraphQLSchemaProvider schema={schemaName}>
      <ActiveDataProviderNameProvider name={schemaName}>
        <ModelMetadataProvider metadata={schema.fieldMetadata}>
          {body}
        </ModelMetadataProvider>
      </ActiveDataProviderNameProvider>
    </ActiveGraphQLSchemaProvider>
  );
}

export function PassthroughChrome({ children }: { children: ReactNode }): ReactNode {
  return <>{children}</>;
}

function layoutRequiresAuth(
  layoutName: string,
  layouts: Record<string, RefineLayoutConfig>,
): boolean {
  return layouts[layoutName]?.requireAuth ?? layoutName !== "public";
}

function authBeforeLoad(
  authProvider: RefineAuthProvider,
  queryClient: QueryClient,
) {
  return async ({ location }: { location: { href: string } }): Promise<void> => {
    const identity = await queryClient
      .ensureQueryData(identityQueryOptions(authProvider))
      .catch(() => null);
    if (identity) return;
    throw redirect({
      to: "/login",
      search: { next: location.href },
      replace: true,
    });
  };
}

function layoutRouteFor(
  route: BaseAddonRoute,
  layoutRoutes: ReadonlyMap<string, AnyRoute>,
): AnyRoute {
  const layoutName = route.layout ?? "console";
  const layout = layoutRoutes.get(layoutName);
  if (!layout) {
    throw new Error(
      `Route "${route.name}" references undeclared layout "${layoutName}".`,
    );
  }
  return layout;
}

function createAddonRouteNode(
  route: BaseAddonRoute,
  parentNode: AnyRoute,
  parentManifestRoute: BaseAddonRoute | undefined,
): AnyRoute {
  return createRoute({
    getParentRoute: () => parentNode,
    path: routePathUnderParent(route, parentManifestRoute),
    ...(route.component ? { component: route.component } : {}),
  });
}

interface NamedRouteNode {
  name: string;
  route: AnyRoute;
}

function refineLayoutRouteId(layoutName: string): string {
  return `_angee_layout_${layoutName}`;
}

function compareRouteNames(
  left: BaseAddonRoute,
  right: BaseAddonRoute,
): number {
  return compareCodePoint(left.name, right.name);
}

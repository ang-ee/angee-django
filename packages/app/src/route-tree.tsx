import {
  ActiveGraphQLSchemaProvider,
  ModelMetadataProvider,
  schemaFieldMetadataWithVocabulary,
  type SchemaFieldMetadata,
} from "@angee/metadata";
import { ActiveDataProviderNameProvider } from "@angee/refine";
import { Button, ErrorBanner } from "@angee/ui";
import { useAppRuntime } from "@angee/ui/runtime";
import { useUiT } from "@angee/ui/i18n";
import type { AuthProvider as RefineAuthProvider } from "@refinedev/core";
import type { QueryClient } from "@tanstack/react-query";
import {
  type AnyRoute,
  Outlet,
  createRoute,
  redirect,
  useRouter,
} from "@tanstack/react-router";
import { createElement, useMemo, type ReactNode } from "react";

import type {
  BaseAddonRoute,
  BaseLayoutProvider,
  RefineLayoutConfig,
} from "./define-base-addon";
import { identityQueryOptions, isUnauthorizedError } from "./providers/auth";
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
  loginPath,
  layoutProviders = [],
}: {
  rootRoute: AnyRoute;
  layoutNames: readonly string[];
  layouts: Record<string, RefineLayoutConfig>;
  schemas: Readonly<Record<string, RouteSchemaConfig>>;
  defaultSchema: string;
  authProvider: RefineAuthProvider;
  queryClient: QueryClient;
  loginPath: string;
  layoutProviders?: readonly BaseLayoutProvider[];
}): Map<string, AnyRoute> {
  const layoutRoutes = new Map<string, AnyRoute>();
  for (const provider of layoutProviders) {
    if (!layouts[provider.layout]) {
      throw new Error(`Layout provider "${provider.id}" references undeclared layout "${provider.layout}".`);
    }
  }
  for (const layoutName of layoutNames) {
    const providers = layoutProviders.filter((provider) => provider.layout === layoutName);
    layoutRoutes.set(
      layoutName,
      createRoute({
        getParentRoute: () => rootRoute,
        id: refineLayoutRouteId(layoutName),
        ...layoutAuthGuard(layoutName, layouts, authProvider, queryClient, loginPath),
        component: () => (
          <RefineLayoutRoute
            layoutName={layoutName}
            layouts={layouts}
            schemas={schemas}
            defaultSchema={defaultSchema}
            providers={providers}
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
  consoleConfinement,
}: {
  routes: readonly BaseAddonRoute[];
  routesByName: ReadonlyMap<string, BaseAddonRoute>;
  layoutRoutes: ReadonlyMap<string, AnyRoute>;
  consoleConfinement?: { allows: (route: BaseAddonRoute, pathname: string) => boolean; home: string };
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
    let ancestor = route;
    while (ancestor.parent) {
      const parent = routesByName.get(ancestor.parent);
      if (!parent) break;
      ancestor = parent;
    }
    const confined = consoleConfinement && (ancestor.layout ?? "console") === "console";
    const node = createAddonRouteNode(
      route,
      parentNode,
      parentManifestRoute,
      confined ? consoleConfinement : undefined,
    );
    routeNodes.set(route.name, node);
    if (route.indexComponent) {
      childrenByParent.set(node, [{
        name: `${route.name}.index`,
        route: createRoute({
          getParentRoute: () => node,
          path: "/",
          component: route.indexComponent,
        }),
      }]);
    }
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
  providers,
}: {
  layoutName: string;
  layouts: Record<string, RefineLayoutConfig>;
  schemas: Readonly<Record<string, RouteSchemaConfig>>;
  defaultSchema: string;
  providers: readonly BaseLayoutProvider[];
}): ReactNode {
  const layout = layouts[layoutName];
  const Chrome = layout?.chrome ?? PassthroughChrome;
  const schemaName = layout?.schema ?? defaultSchema;
  const schema = schemas[schemaName];
  const { vocabulary } = useAppRuntime();
  const metadata = useMemo(() => schema
    ? schemaFieldMetadataWithVocabulary(schema.fieldMetadata, vocabulary.resources)
    : undefined, [schema, vocabulary]);
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
        <ModelMetadataProvider metadata={metadata}>
          {providers.reduceRight<ReactNode>(
            (children, provider) => createElement(provider.component, { key: provider.id, children }),
            body,
          )}
        </ModelMetadataProvider>
      </ActiveDataProviderNameProvider>
    </ActiveGraphQLSchemaProvider>
  );
}

export function PassthroughChrome({ children }: { children: ReactNode }): ReactNode {
  return <>{children}</>;
}

/** The sign-in guard of a declared layout's routes; none for a public or undeclared layout. */
export function layoutAuthGuard(
  layoutName: string,
  layouts: Record<string, RefineLayoutConfig>,
  authProvider: RefineAuthProvider,
  queryClient: QueryClient,
  loginPath: string,
) {
  const layout = layouts[layoutName];
  return layout && (layout.requireAuth ?? layoutName !== "public")
    ? { beforeLoad: authBeforeLoad(authProvider, queryClient, loginPath), errorComponent: authRouteError(queryClient, authProvider) }
    : {};
}

export function authBeforeLoad(
  authProvider: RefineAuthProvider,
  queryClient: QueryClient,
  loginPath: string,
) {
  return async ({ location }: { location: { href: string } }): Promise<void> => {
    let identity;
    try {
      identity = await queryClient.ensureQueryData(identityQueryOptions(authProvider));
    } catch (error) {
      if (!isUnauthorizedError(error)) throw new AuthIdentityCheckError();
      identity = null;
    }
    if (identity) return;
    throw redirect({
      to: loginPath,
      search: { next: location.href },
      replace: true,
    });
  };
}

class AuthIdentityCheckError extends Error {
  constructor() {
    super("The server could not confirm the current session.");
    this.name = "AuthIdentityCheckError";
  }
}

export function authRouteError(queryClient: QueryClient, authProvider: RefineAuthProvider) {
  return function AuthRouteError({ error }: { error: unknown }): ReactNode {
    const router = useRouter();
    const t = useUiT();
    if (!(error instanceof AuthIdentityCheckError)) throw error;
    const retry = async () => {
      queryClient.removeQueries({
        queryKey: identityQueryOptions(authProvider).queryKey,
        exact: true,
      });
      await router.invalidate();
    };
    return (
      <div className="mx-auto grid w-full max-w-xl gap-3 p-6">
        <ErrorBanner
          title={t("auth.sessionCheckFailed")}
          description={t("auth.sessionCheckFailedDescription")}
          actions={<Button type="button" size="sm" variant="secondary" onClick={() => { void retry(); }}>{t("auth.retrySessionCheck")}</Button>}
        />
      </div>
    );
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
  confinement?: { allows: (route: BaseAddonRoute, pathname: string) => boolean; home: string },
): AnyRoute {
  return createRoute({
    getParentRoute: () => parentNode,
    path: routePathUnderParent(route, parentManifestRoute),
    ...(confinement ? { beforeLoad: ({ location }) => {
      if (!confinement.allows(route, location.pathname)) {
        throw redirect({ to: confinement.home, replace: true });
      }
    } } : {}),
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

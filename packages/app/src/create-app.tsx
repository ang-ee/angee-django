import {
  ActiveGraphQLSchemaProvider,
  createAngeeAccessControlProvider,
  refineResourcesFromAngeeSchemaMetadata,
  type RefineResourceMetadata,
} from "@angee/resources";
import {
  StrictMode,
  useEffect,
  useMemo,
  type ComponentType,
  type ReactNode,
  } from "react";
import { createRoot,
  type Root } from "react-dom/client";
import { Refine,
  type AuthProvider as RefineAuthProvider,
  type DataProvider as RefineDataProvider,
  type DataProviders,
  type ResourceProps } from "@refinedev/core";
import { QueryClient, keepPreviousData, type QueryClientConfig } from "@tanstack/react-query";
import {
  type AnyRoute,
  type AnyRouter,
  Outlet,
  type RouteComponent,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
  useNavigate,
  } from "@tanstack/react-router";
import { NuqsAdapter } from "nuqs/adapters/tanstack-router";
import {
  AuthProvider,
  UserPreferencesProvider,
  createAngeeAuthProvider,
  createAngeeI18nProvider,
  identityQueryOptions,
  useRuntimeAuthState,
  useUserPreferences,
  type I18nResources,
} from "@angee/refine";
import {
  ModelMetadataProvider,
  defineAngeeSchemaMetadata,
  schemaFieldMetadataFromAngeeSchemaMetadata,
  type AngeeSchemaMetadata,
  type SchemaFieldMetadata,
} from "@angee/resources";
import {
  OperationDocumentsProvider,
  createAngeeHasuraDataProviders,
  createAngeeHasuraLiveProvider,
  tanStackRouterProvider,
  type AngeeHasuraSchemaConfig,
  type SchemaOperationDocuments,
  } from "@angee/refine";
import {
  AppRuntimeProvider,
  type AppRuntime,
  type ChatterRoute,
  type ComposedMenuItem,
  type SlotContribution,
  } from "@angee/ui/runtime";
import {
  composeAddons,
  mergeSlotContributions,
  type AddonManifest,
  type AddonRoute,
} from "./define-addon";
import {
  ModalsHost,
  ToastProvider,
  useRefineNotificationProvider,
} from "@angee/ui/feedback/index";
import { readAppRailPreferences } from "@angee/ui/chrome/app-rail-preferences";
import { baseIcons } from "@angee/ui/chrome/icon-registry";
import { LoadingPanel } from "@angee/ui/fragments/index";
import {
  MenuTree,
  type BaseMenuItem,
  type ChromeMenuItem,
  type ChromeMenuNode,
} from "@angee/ui/chrome/menu-tree";
import { useChromeMenuTree } from "@angee/ui/chrome/refine-menu";
import { enBaseBundle } from "@angee/ui/i18n";
import { type PreviewProvider } from "@angee/ui/preview/index";
import { defaultWidgets } from "@angee/ui/widgets/index";

/** A route that also carries the page component the chrome renders. */
export interface BaseAddonRoute extends AddonRoute {
  /**
   * The page the chrome renders for this route — the router's own route-component
   * type, so a `lazyRouteComponent(() => import(...))` (carrying `.preload`) drops
   * straight in next to an eager function component.
   */
  component?: RouteComponent;
  /**
   * Menu item id whose trail seeds chrome for routes outside the menu, or
   * disambiguates chrome derivation when multiple menu items target this route.
   */
  menu?: string;
  title?: ReactNode;
  icon?: string;
}

/** An addon manifest whose routes carry their page components. */
export interface BaseAddon
  extends Omit<AddonManifest, "routes" | "menus" | "previews"> {
  routes?: readonly BaseAddonRoute[];
  menus?: readonly BaseMenuItem[];
  /**
   * Full preview renderers. The SDK manifest tracks only the contribution id
   * (for collision detection); the rendered binding owns `PreviewProvider`, so
   * its addon authors the whole renderer here — `PreviewPane` reads it back.
   */
  previews?: readonly PreviewProvider[];
  /**
   * Refine data providers keyed by provider name. The SDK manifest tracks only
   * the name (for collision detection); the rendered binding owns the live
   * `DataProvider`, so its addon authors the whole provider here — `createApp`
   * registers it alongside the schema-named providers.
   */
  dataProviders?: Readonly<Record<string, Required<RefineDataProvider>>>;
}

/**
 * Declare a rendered (base-binding) addon — the one seam every addon's manifest
 * goes through. The rendered analog of the SDK's headless `defineAddon`: it
 * type-checks the literal against {@link BaseAddon} (routes carrying React
 * components) and returns it unchanged, so addons `defineBaseAddon({...})`
 * instead of annotating `const x: BaseAddon = {...}`.
 */
export function defineBaseAddon(addon: BaseAddon): BaseAddon {
  return addon;
}

/** Props passed from the active route into a refine layout chrome component. */
export interface RefineLayoutChromeProps {
  children: ReactNode;
}

/** A refine layout registered with `createApp`: chrome, auth, and schema policy. */
export interface RefineLayoutConfig {
  /** Chrome wrapping route bodies (e.g. `ConsoleLayout`). */
  chrome?: ComponentType<RefineLayoutChromeProps>;
  /** Gate routes behind sign-in. Defaults to `true` for every layout but `public`. */
  requireAuth?: boolean;
  /**
   * GraphQL schema this layout's routes read from; reads inside the layout inherit
   * that client. Defaults to the app's `defaultSchema`, so the common console
   * surface needs no override and only the public layout pins itself to `public`.
   */
  schema?: string;
}

export interface CreateAppInput {
  addons: readonly BaseAddon[];
  layouts: Record<string, RefineLayoutConfig>;
  /** One client config per named schema (url, ws endpoint, cache). */
  schemas: Record<string, AngeeAppSchemaConfig>;
  /** Schema bound to the app subtree's reads. Defaults to `public`. */
  defaultSchema?: string;
  /** Schema carrying the change subscriptions. Defaults to `console`. */
  subscriptionSchema?: string;
  /** Where `/` redirects. Defaults to the first non-public route's path. */
  home?: string;
  /** Host-level UI slot contributions, merged with the addons'. */
  slots?: readonly SlotContribution[];
}

export type AngeeAppSchemaConfig =
  Omit<AngeeHasuraSchemaConfig, "metadata"> & {
    /** Generated schema metadata imported from emitted JSON. */
    metadata?: unknown;
    /** Generated operation documents imported from emitted project codegen. */
    operationDocuments?: SchemaOperationDocuments;
  };

type NormalizedAngeeAppSchemaConfig =
  Omit<AngeeAppSchemaConfig, "metadata"> & {
    metadata?: AngeeSchemaMetadata;
    fieldMetadata: SchemaFieldMetadata;
  };

export interface AngeeApp {
  router: AnyRouter;
  mount(target: string | Element): Root;
}

/**
 * The app-owned react-query client config. createApp builds ONE `QueryClient`
 * from this and shares it with both `<Refine reactQuery.clientConfig>` and the
 * route gate's `beforeLoad`, so the auth gate and the in-app identity read hit
 * the same cache (one `current_user` fetch). Refine layers its own defaults onto
 * a config *object* but uses a supplied `QueryClient` *instance* as-is, so the
 * two refine defaults are restated here: `refetchOnWindowFocus: false` and
 * `placeholderData: keepPreviousData` (keeps list pagination smooth). A short
 * `staleTime` retires the every-mount refetch churn (react-query refetches on
 * mount over `staleTime: 0`) while freshness keeps riding refine's mutation
 * invalidation and the live provider's `changes()` subscriptions; `gcTime` holds
 * unmounted query data for fast back-navigation. A flat app-wide default is the
 * right shape here — the per-resource "is this model live" fact is owned by the
 * `@angee/resources` metadata, and any query needing different staleness (e.g.
 * the identity query's `staleTime: Infinity`) overrides it through its own
 * per-hook `queryOptions`.
 */
const APP_QUERY_CLIENT_CONFIG: QueryClientConfig = {
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      placeholderData: keepPreviousData,
      staleTime: 30_000,
      gcTime: 600_000,
    },
  },
};

/**
 * `createApp` — the single composition root. It merges the addon manifests into
 * one runtime (routes · route-resolved menus · widgets ·
 * i18n · slots), owns the provider stack (GraphQL clients · runtime · live
 * invalidation · auth), builds the router, and mounts one persistent layout
 * route per refine layout. The host writes one
 * `createApp({...}).mount(...)`.
 */
export function createApp(input: CreateAppInput): AngeeApp {
  const composed = composeAddons([
    { id: "base", icons: baseIcons },
    ...input.addons,
  ]);
  const routes = composed.routes as readonly BaseAddonRoute[];
  const pathByName = new Map(
    routes.map((route) => [route.name, route.path]),
  );
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const menus = resolveMenuRouteTargets(
    composed.menus as readonly ChromeMenuItem[],
    pathByName,
  );
  const menuTree = MenuTree.from(menus);

  const routeResourceProjection = refineRouteResourceProjection(
    routes,
    menuTree,
  );

  const defaultSchema = input.defaultSchema ?? "public";
  const subscriptionSchema = input.subscriptionSchema ?? "console";
  const schemas = normalizeSchemaConfigs(input.schemas);
  const resourceTypesByModel = resourceTypesByModelLabel(schemas);

  const runtime: AppRuntime = {
    widgets: { ...defaultWidgets, ...composed.widgets },
    // Seed the base namespace under the merged addon bundles; an addon key wins.
    i18n: mergeI18n(enBaseBundle, composed.i18n),
    icons: composed.icons,
    forms: composed.forms,
    chatter: composed.chatter,
    chatterRoutes: chatterRouteIndex(routes, resourceTypesByModel),
    slots: mergeSlotContributions(composed.slots, input.slots ?? []),
    // Built-in renderers are universal (PreviewPane always includes them); the
    // runtime carries only addon-contributed providers.
    previews: composed.previews,
    drawers: composed.drawers,
    routesByResource: resourceRouteIndex(routes),
  };
  const operationDocuments = operationDocumentsForSchemas(schemas);
  const refineResources = refineResourcesForSchemas(
    schemas,
    runtime.routesByResource,
    routeResourceProjection.metadataByResource,
  );
  // Menu route resources seed refine's tree in authored addon/menu order; schema
  // CRUD resources then attach under those parents without reordering sections.
  const refineResourceRegistry = [
    ...routeResourceProjection.resources,
    ...refineResources,
  ];
  const refineDataProviders = mergeAddonDataProviders(
    createAngeeHasuraDataProviders(schemas, defaultSchema),
    composed.dataProviders as Readonly<
      Record<string, Required<RefineDataProvider>>
    >,
  );
  const refineLiveProvider = createLiveProviderForSchema(
    schemas,
    subscriptionSchema,
  );
  const authSchema = authSchemaNameForSchemas(schemas, defaultSchema);
  const refineAuthProvider = createAuthProviderForSchema(schemas, authSchema);
  const refineI18nProvider = createAngeeI18nProvider(runtime.i18n);
  const refineAccessControlProvider = createAngeeAccessControlProvider(
    refineResourceRegistry,
  );
  // The one QueryClient instance createApp owns (per `@angee/app` `index.ts`):
  // shared by `<Refine>` and the route gate so identity is fetched once.
  const queryClient = new QueryClient(APP_QUERY_CLIENT_CONFIG);

  const home =
    resolvePath(input.home, pathByName) ??
    routes.find((route) => route.layout !== "public")?.path ??
    "/";

  function RootOutlet(): ReactNode {
    return (
      <NuqsAdapter>
        <OperationDocumentsProvider documents={operationDocuments}>
          <AppRuntimeProvider runtime={runtime}>
            <ModalsHost>
              <ToastProvider>
                <RefineRoot />
              </ToastProvider>
            </ModalsHost>
          </AppRuntimeProvider>
        </OperationDocumentsProvider>
      </NuqsAdapter>
    );
  }

  function RefineRoot(): ReactNode {
    const refineNotificationProvider = useRefineNotificationProvider();
    return (
      <Refine
        authProvider={refineAuthProvider}
        accessControlProvider={refineAccessControlProvider}
        dataProvider={refineDataProviders}
        i18nProvider={refineI18nProvider}
        liveProvider={refineLiveProvider}
        notificationProvider={refineNotificationProvider}
        resources={refineResourceRegistry}
        routerProvider={tanStackRouterProvider}
        options={{
          liveMode: refineLiveProvider ? "auto" : "off",
          syncWithLocation: false,
          reactQuery: { clientConfig: queryClient },
        }}
      >
        <AppFrame
          authSchema={authSchema}
        >
          <Outlet />
        </AppFrame>
      </Refine>
    );
  }

  const rootRoute = createRootRoute({ component: RootOutlet });

  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <HomeRedirect fallback={home} />,
  });

  const layoutRoutes = createLayoutRoutes({
    rootRoute,
    layoutNames: layoutNamesForRoutes(input.layouts),
    layouts: input.layouts,
    schemas,
    defaultSchema,
    authProvider: refineAuthProvider,
    queryClient,
  });
  createAddonRouteNodes({
    routes,
    routesByName,
    layoutRoutes,
  });

  const router = createRouter({
    routeTree: rootRoute.addChildren([
      indexRoute,
      ...[...layoutRoutes.entries()]
        .sort(([left], [right]) => compareCodePoint(left, right))
        .map(([, route]) => route),
    ]),
    history: typeof window === "undefined" ? createMemoryHistory() : undefined,
    parseSearch: parseFlatSearch,
    stringifySearch: stringifyFlatSearch,
    defaultPreload: false,
    // The router owns the route-loading fallback once: every code-split match
    // (and any future loader-bearing route, after `defaultPendingMs`) renders
    // this inside its parent layout's <Outlet/>, so the chrome stays mounted.
    defaultPendingComponent: () => <LoadingPanel />,
  });

  return {
    router,
    mount(target: string | Element): Root {
      const element =
        typeof target === "string" ? document.querySelector(target) : target;
      if (!element) {
        throw new Error(`createApp().mount: no element matched ${String(target)}`);
      }
      const root = createRoot(element);
      root.render(
        <StrictMode>
          <RouterProvider router={router} />
        </StrictMode>,
      );
      return root;
    },
  };
}

function refineResourcesForSchemas(
  schemas: Readonly<Record<string, NormalizedAngeeAppSchemaConfig>>,
  routesByResource: Readonly<Record<string, string>>,
  metadataByResource: Readonly<Record<string, RefineResourceMetadata>>,
) {
  return Object.values(schemas).flatMap((schema) =>
    refineResourcesFromAngeeSchemaMetadata(schema.metadata, {
      pathsByResource: routesByResource,
      metadataByResource,
    }),
  );
}

interface RefineRouteResourceProjection {
  resources: readonly ResourceProps[];
  metadataByResource: Readonly<Record<string, RefineResourceMetadata>>;
}

function refineRouteResourceProjection(
  routes: readonly BaseAddonRoute[],
  menuTree: MenuTree,
): RefineRouteResourceProjection {
  const resourcesByIdentifier = new Map<string, ResourceProps>();
  const metadataByResource: Record<string, RefineResourceMetadata> = {};
  const appRootIds = new Set(menuTree.roots.map((item) => item.id));
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const childrenByParentName = childRoutesByParentName(routes);

  for (const node of menuTree.byId.values()) {
    const menuTrail = menuTree.trailFor(node.id);
    menuTrail.forEach((item, index) => {
      addMenuRouteResource(
        resourcesByIdentifier,
        item,
        menuTrail[index - 1],
        appRootIds.has(item.id),
        menuRouteShowPath(item, routesByName, childrenByParentName),
      );
    });
  }

  for (const route of routes) {
    if (!route.resource) continue;
    const selected = menuNodeForRouteResource(route, menuTree);
    const trail = selected
      ? breadcrumbTrailFromMenuTrail(menuTree.trailFor(selected.id))
      : [];
    const leaf = trail.at(-1);
    const parent = trail.length > 1 ? trail[trail.length - 2] : undefined;
    metadataByResource[route.resource] = {
      ...(leaf ? { label: leaf.displayLabel } : routeLabel(route)),
      ...(leaf ? { icon: leaf.iconName } : routeIcon(route)),
      ...(parent ? { parent: menuRouteResourceIdentifier(parent.id) } : {}),
    };
  }

  return {
    resources: [...resourcesByIdentifier.values()],
    metadataByResource,
  };
}

function addMenuRouteResource(
  resourcesByIdentifier: Map<string, ResourceProps>,
  item: ChromeMenuNode,
  parent: ChromeMenuNode | undefined,
  appRoot: boolean,
  showPath: string | undefined,
): void {
  const target = item.target;
  if (!target || target === "#") return;
  const identifier = menuRouteResourceIdentifier(item.id);
  const existing = resourcesByIdentifier.get(identifier);
  if (existing) {
    if (appRoot) {
      existing.meta = {
        ...existing.meta,
        appRoot: true,
      };
    }
    return;
  }
  resourcesByIdentifier.set(identifier, {
    name: identifier,
    identifier,
    list: refineRoutePathForTanStack(target),
    ...(showPath ? { show: refineRoutePathForTanStack(showPath) } : {}),
    meta: {
      label: item.displayLabel,
      icon: item.iconName,
      menuId: item.id,
      ...(appRoot ? { appRoot: true } : {}),
      ...(item.description ? { description: item.description } : {}),
      ...(item.group ? { group: item.group } : {}),
      ...(item.sidebar !== undefined ? { sidebar: item.sidebar } : {}),
      ...(item.status ? { status: item.status } : {}),
      ...(item.tone ? { tone: item.tone } : {}),
      ...(item.badge !== undefined ? { badge: item.badge } : {}),
      ...(parent ? { parent: menuRouteResourceIdentifier(parent.id) } : {}),
    },
  });
}

function menuNodeForRouteResource(
  route: BaseAddonRoute,
  menuTree: MenuTree,
): ChromeMenuNode | undefined {
  if (route.menu) {
    const selected = menuTree.byId.get(route.menu);
    if (!selected) {
      throw new Error(
        `Route "${route.name}" references unknown menu item "${route.menu}".`,
      );
    }
    const refs = menuTree.itemsForRoute(route.name);
    if (refs.length > 0 && !refs.some((item) => item.id === selected.id)) {
      throw new Error(
        `Route "${route.name}" sets menu "${route.menu}", but that item does not reference the route.`,
      );
    }
    return selected;
  }
  const refs = menuTree.itemsForRoute(route.name);
  return refs.length === 1 ? refs[0] : undefined;
}

function childRoutesByParentName(
  routes: readonly BaseAddonRoute[],
): ReadonlyMap<string, readonly BaseAddonRoute[]> {
  const children = new Map<string, BaseAddonRoute[]>();
  for (const route of routes) {
    if (!route.parent) continue;
    children.set(route.parent, [...(children.get(route.parent) ?? []), route]);
  }
  return children;
}

function menuRouteShowPath(
  item: ChromeMenuNode,
  routesByName: ReadonlyMap<string, BaseAddonRoute>,
  childrenByParentName: ReadonlyMap<string, readonly BaseAddonRoute[]>,
): string | undefined {
  const route = item.route ? routesByName.get(item.route) : undefined;
  if (!route) return undefined;
  const child = childrenByParentName
    .get(route.name)
    ?.find((candidate) => routeChildHasTrailingParam(candidate, route));
  return child ? fullRoutePath(child, route) : undefined;
}

function chatterRouteIndex(
  routes: readonly BaseAddonRoute[],
  resourceTypesByModel: Readonly<Record<string, string>>,
): readonly ChatterRoute[] {
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const childrenByParentName = childRoutesByParentName(routes);
  return routes.map((route) => {
    const parent = route.parent ? routesByName.get(route.parent) : undefined;
    const path = fullRoutePath(route, parent);
    const recordParam = trailingRouteParamName(path);
    const modelLabel = inheritedRouteResource(route, routesByName);
    return {
      name: route.name,
      path,
      viewType: routeChatterViewType(
        route,
        routesByName,
        childrenByParentName,
        resourceTypesByModel,
      ),
      ...(modelLabel ? { modelLabel } : {}),
      ...(recordParam ? { recordParam } : {}),
    };
  });
}

function routeChatterViewType(
  route: BaseAddonRoute,
  routesByName: ReadonlyMap<string, BaseAddonRoute>,
  childrenByParentName: ReadonlyMap<string, readonly BaseAddonRoute[]>,
  resourceTypesByModel: Readonly<Record<string, string>>,
): string {
  const resource = inheritedRouteResource(route, routesByName);
  if (resource) {
    return resourceTypesByModel[resource] ?? resourceTypeFromModelLabel(resource);
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

function resourceTypesByModelLabel(
  schemas: Readonly<Record<string, NormalizedAngeeAppSchemaConfig>>,
): Record<string, string> {
  const byModel: Record<string, string> = {};
  for (const schema of Object.values(schemas)) {
    for (const resource of schema.metadata?.angee?.resources ?? []) {
      const resourceType = resource.resourceType;
      if (!resourceType) continue;
      byModel[resource.modelLabel] = resourceType;
      byModel[resource.modelName] = resourceType;
    }
  }
  return byModel;
}

function resourceTypeFromModelLabel(modelLabel: string): string {
  const parts = modelLabel.split(".");
  const modelName = parts.pop();
  const appLabel = parts.join(".");
  if (!appLabel || !modelName) return routeNameViewType(modelLabel);
  return `${appLabel}/${snakeCase(modelName)}`;
}

function routeNameViewType(routeName: string): string {
  return routeName.split(".").map(snakeCase).join("/");
}

function snakeCase(value: string): string {
  return value
    .replace(/-/g, "_")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1_$2")
    .replace(/([a-z\d])([A-Z])/g, "$1_$2")
    .toLowerCase();
}

function routeChildHasTrailingParam(
  route: BaseAddonRoute,
  parent: BaseAddonRoute,
): boolean {
  const path = routePathUnderParent(route, parent);
  return Boolean(trailingRouteParamName(path));
}

function fullRoutePath(
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

function menuRouteResourceIdentifier(menuId: string): string {
  return `menu:${menuId}`;
}

function refineRoutePathForTanStack(path: string): string {
  return path.replace(/(^|\/)\$([^/?#]+)/g, "$1:$2");
}

function routeLabel(route: BaseAddonRoute): RefineResourceMetadata {
  return typeof route.title === "string" ? { label: route.title } : {};
}

function routeIcon(route: BaseAddonRoute): RefineResourceMetadata {
  return route.icon ? { icon: route.icon } : {};
}

function normalizeSchemaConfigs(
  schemas: Readonly<Record<string, AngeeAppSchemaConfig>>,
): Record<string, NormalizedAngeeAppSchemaConfig> {
  return Object.fromEntries(
    Object.entries(schemas).map(([name, schema]) => [
      name,
      normalizeSchemaConfig(schema),
    ]),
  );
}

function normalizeSchemaConfig(
  schema: AngeeAppSchemaConfig,
): NormalizedAngeeAppSchemaConfig {
  const { metadata, ...config } = schema;
  const normalizedMetadata =
    metadata == null ? undefined : defineAngeeSchemaMetadata(metadata);
  return {
    ...config,
    fieldMetadata: schemaFieldMetadataFromAngeeSchemaMetadata(normalizedMetadata),
    ...(normalizedMetadata == null ? {} : { metadata: normalizedMetadata }),
  };
}

function operationDocumentsForSchemas(
  schemas: Readonly<Record<string, NormalizedAngeeAppSchemaConfig>>,
): Readonly<Record<string, SchemaOperationDocuments | undefined>> {
  return Object.fromEntries(
    Object.entries(schemas).map(([name, schema]) => [
      name,
      schema.operationDocuments,
    ]),
  );
}

/**
 * Register addon-contributed data providers next to the schema-named ones. A
 * schema name (and the reserved `default` key) is owned by `createApp`'s schema
 * config, so an addon claiming one would silently shadow it — that is a
 * build-time error, matching the registry collision discipline elsewhere.
 */
function mergeAddonDataProviders(
  schemaProviders: DataProviders,
  addonProviders: Readonly<Record<string, Required<RefineDataProvider>>>,
): DataProviders {
  const merged: DataProviders = { ...schemaProviders };
  for (const [name, provider] of Object.entries(addonProviders)) {
    if (Object.prototype.hasOwnProperty.call(schemaProviders, name)) {
      throw new Error(
        `Addon data provider "${name}" collides with a schema-named provider; ` +
          "rename the provider so it does not shadow a configured schema.",
      );
    }
    merged[name] = provider;
  }
  return merged;
}

function createLiveProviderForSchema(
  schemas: Readonly<Record<string, NormalizedAngeeAppSchemaConfig>>,
  subscriptionSchema: string,
) {
  const schema = schemas[subscriptionSchema];
  if (!schema?.live) return undefined;
  const liveOptions = schema.live === true
    ? {
        url: schema.url,
      }
    : schema.live;
  return createAngeeHasuraLiveProvider({
    ...liveOptions,
    resources: schema.metadata?.angee?.resources ?? [],
  });
}

function authSchemaNameForSchemas(
  schemas: Readonly<Record<string, NormalizedAngeeAppSchemaConfig>>,
  defaultSchema: string,
): string {
  if (schemas.public) return "public";
  if (schemas[defaultSchema]) return defaultSchema;
  const first = Object.keys(schemas).sort(compareCodePoint)[0];
  if (!first) throw new Error("createApp requires at least one schema.");
  return first;
}

function createAuthProviderForSchema(
  schemas: Readonly<Record<string, NormalizedAngeeAppSchemaConfig>>,
  authSchema: string,
): RefineAuthProvider {
  const schema = schemas[authSchema];
  if (!schema) {
    throw new Error(`No GraphQL schema config for auth schema "${authSchema}".`);
  }
  return createAngeeAuthProvider(schema);
}

// Keep search values flat and unquoted so login next round-trips raw and
// resource-view values read like status:year.
export function parseFlatSearch(searchStr: string): Record<string, string> {
  const source = searchStr.startsWith("?") ? searchStr.slice(1) : searchStr;
  const params = new URLSearchParams(source);
  const search: Record<string, string> = {};
  for (const [key, value] of params.entries()) {
    search[key] = value;
  }
  return search;
}

export function stringifyFlatSearch(search: Record<string, unknown>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(search)) {
    if (value == null || value === "") continue;
    params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

/**
 * The provider frame inside the client pool: resolve the current actor, open the
 * change subscriptions on the subscription schema's client, and expose the
 * runtime and auth state to every route.
 */
function AppFrame({
  authSchema,
  children,
}: {
  authSchema: string;
  children: ReactNode;
}): ReactNode {
  const { auth } = useRuntimeAuthState();
  return (
    <AuthProvider auth={auth}>
      <UserPreferencesProvider dataProviderName={authSchema}>
        {children}
      </UserPreferencesProvider>
    </AuthProvider>
  );
}

function HomeRedirect({ fallback }: { fallback: string }): ReactNode {
  const menuTree = useChromeMenuTree();
  const { preferences } = useUserPreferences();
  const target = useMemo(() => {
    const defaultItemId = readAppRailPreferences(preferences).defaultItemId;
    if (!defaultItemId) return fallback;
    const item = menuTree
      .railMenuItems()
      .find((node) => node.id === defaultItemId);
    return item?.target ?? fallback;
  }, [fallback, menuTree, preferences]);
  return <Redirect to={target} />;
}

function RefineLayoutRoute({
  layoutName,
  layouts,
  schemas,
  defaultSchema,
}: {
  layoutName: string;
  layouts: Record<string, RefineLayoutConfig>;
  schemas: Readonly<Record<string, NormalizedAngeeAppSchemaConfig>>;
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
  // Bind the route to its layout's schema metadata/provider name for refine calls.
  return (
    <ActiveGraphQLSchemaProvider schema={schemaName}>
      <ModelMetadataProvider metadata={schema.fieldMetadata}>
        {body}
      </ModelMetadataProvider>
    </ActiveGraphQLSchemaProvider>
  );
}

function createLayoutRoutes({
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
  schemas: Readonly<Record<string, NormalizedAngeeAppSchemaConfig>>;
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
    // Dispatch to the identity owner instead of a raw `check()` POST:
    // `ensureQueryData` populates (or reuses) the same `["auth","identity"]`
    // entry `useGetIdentity` reads, so the gate and the in-app identity read
    // share one `current_user` fetch — instant on warm nav. A null/failed
    // identity redirects to login, preserving `next`.
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

function createAddonRouteNodes({
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

function layoutRouteFor(
  route: BaseAddonRoute,
  layoutRoutes: ReadonlyMap<string, AnyRoute>,
): AnyRoute {
  const layout = layoutRoutes.get(route.layout);
  if (!layout) {
    throw new Error(
      `Route "${route.name}" references undeclared layout "${route.layout}".`,
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
    // Pass the page component straight through: a `lazyRouteComponent`'s
    // `.preload` survives (the old `() => <Page/>` wrapper shadowed it), and an
    // eager component is unchanged.
    ...(route.component ? { component: route.component } : {}),
  });
}

interface NamedRouteNode {
  name: string;
  route: AnyRoute;
}

function layoutNamesForRoutes(layouts: Record<string, RefineLayoutConfig>): readonly string[] {
  return Object.keys(layouts).sort(compareCodePoint);
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

function compareCodePoint(left: string, right: string): number {
  if (left < right) return -1;
  if (left > right) return 1;
  return 0;
}

function Redirect({ to }: { to: string }): ReactNode {
  const navigate = useNavigate();
  useEffect(() => {
    void navigate({ to });
  }, [to, navigate]);
  return null;
}

export function PassthroughChrome({ children }: RefineLayoutChromeProps): ReactNode {
  return <>{children}</>;
}

function resolveMenuRouteTargets(
  items: readonly ComposedMenuItem[],
  pathByName: ReadonlyMap<string, string>,
): readonly ComposedMenuItem[] {
  return items.map((item) => resolveMenuRouteTarget(item, pathByName));
}

function resolveMenuRouteTarget(
  item: ComposedMenuItem,
  pathByName: ReadonlyMap<string, string>,
): ComposedMenuItem {
  const itemId = item.id;
  if (item.route && item.to !== undefined) {
    throw new Error(
      `Menu item "${itemId}" declares both route and to; use exactly one target owner.`,
    );
  }
  const routePath = item.route ? pathByName.get(item.route) : undefined;
  if (item.route && !routePath) {
    throw new Error(
      `Menu item "${itemId}" references unknown route "${item.route}".`,
    );
  }
  return {
    ...item,
    to: routePath ?? item.to,
    children: item.children
      ? resolveMenuRouteTargets(item.children, pathByName)
      : item.children,
  };
}

function breadcrumbTrailFromMenuTrail(
  trail: readonly ChromeMenuNode[],
): readonly ChromeMenuNode[] {
  const items: ChromeMenuNode[] = [];
  for (const item of trail) {
    const previous = items.at(-1);
    if (
      previous &&
      previous.displayLabel === item.displayLabel &&
      previous.target === item.target
    ) {
      items[items.length - 1] = item;
      continue;
    }
    items.push(item);
  }
  return items;
}

/**
 * Index each resource to the base path of the collection route that lists it (the
 * `resource`-tagged route), for relation-follow navigation. A resource may be
 * claimed by only one route — a second claim is a build-time error, matching the
 * registry collision discipline elsewhere.
 */
function resourceRouteIndex(
  routes: readonly BaseAddonRoute[],
): Record<string, string> {
  const byResource: Record<string, string> = {};
  for (const route of routes) {
    if (!route.resource) continue;
    if (Object.prototype.hasOwnProperty.call(byResource, route.resource)) {
      throw new Error(
        `Route "${route.name}" claims resource "${route.resource}" already claimed by another route.`,
      );
    }
    byResource[route.resource] = route.path;
  }
  return byResource;
}

function routePathUnderParent(
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

function normalizeRoutePath(path: string): string {
  if (path === "/") return "/";
  const withLeadingSlash = path.startsWith("/") ? path : `/${path}`;
  return withLeadingSlash.replace(/\/+$/, "");
}

function trailingRouteParamName(path: string): string | undefined {
  const segment = normalizeRoutePath(path).split("/").at(-1);
  return segment?.startsWith("$") ? segment.slice(1) || undefined : undefined;
}

function resolvePath(
  nameOrPath: string | undefined,
  pathByName: Map<string, string>,
): string | undefined {
  if (!nameOrPath) return undefined;
  return pathByName.get(nameOrPath) ?? nameOrPath;
}

function mergeI18n(base: I18nResources, over: I18nResources): I18nResources {
  const merged: Record<string, Record<string, string>> = {};
  for (const [namespace, messages] of Object.entries(base)) {
    merged[namespace] = { ...messages };
  }
  for (const [namespace, messages] of Object.entries(over)) {
    merged[namespace] = { ...(merged[namespace] ?? {}), ...messages };
  }
  return merged;
}

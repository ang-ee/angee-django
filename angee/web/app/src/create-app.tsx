import {
  createAngeeAccessControlProvider,
  dataResourcesFromAngeeSchemaMetadata,
  defineAngeeSchemaMetadata,
  schemaFieldMetadataFromAngeeSchemaMetadata,
  type AngeeSchemaMetadata,
  type SchemaFieldMetadata,
} from "@angee/metadata";
import {
  type I18nResources,
  OperationDocumentsProvider,
  createAngeeHasuraDataProviders,
  createAngeeHasuraLiveProvider,
  tanStackRouterProvider,
  type AngeeHasuraSchemaConfig,
  type SchemaOperationDocuments,
} from "@angee/refine";
import {
  Refine,
  type AuthProvider as RefineAuthProvider,
  type DataProvider as RefineDataProvider,
  type DataProviders,
} from "@refinedev/core";
import {
  QueryClient,
  keepPreviousData,
  type QueryClientConfig,
} from "@tanstack/react-query";
import {
  type AnyRouter,
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  useNavigate,
} from "@tanstack/react-router";
import {
  StrictMode,
  useEffect,
  useMemo,
  type ReactNode,
} from "react";
import {
  createRoot,
  type Root,
} from "react-dom/client";
import { NuqsAdapter } from "nuqs/adapters/tanstack-router";
import {
  AppRuntimeProvider,
  type AppRuntime,
  type ComposedMenuItem,
  type SlotContribution,
} from "@angee/ui/runtime";
import {
  composeAddons,
  mergeSlotContributions,
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
  type ChromeMenuItem,
} from "@angee/ui/chrome/menu-tree";
import { useChromeMenuTree } from "@angee/ui/chrome/refine-menu";
import { enUiBundle } from "@angee/ui/i18n";
import { defaultWidgets } from "@angee/ui/widgets/index";
import { createAngeeI18nRuntime } from "./providers/i18n";
import {
  type BaseAddon,
  type BaseAddonRoute,
  type RefineLayoutConfig,
} from "./define-base-addon";
import {
  AuthStateProvider,
  UserPreferencesProvider,
  createAngeeAuthProvider,
  useLogoutAction,
  useRuntimeAuthState,
  useUserPreferences,
  type AuthState,
} from "./providers/auth";
import {
  parseFlatSearch,
  stringifyFlatSearch,
} from "./search-codec";
import {
  resolvePath,
} from "./route-paths";
import {
  refineResourcesForSchemas,
  refineRouteResourceProjection,
  resourceRouteIndex,
} from "./resource-projection";
import { chatterRouteIndex } from "./chatter-routes";
import {
  compareCodePoint,
  createAddonRouteNodes,
  createLayoutRoutes,
  layoutNamesForRoutes,
} from "./route-tree";

export {
  defineBaseAddon,
  resourcePageRoutes,
  type BaseAddon,
  type BaseAddonRoute,
  type ResourcePageRoutesOptions,
  type RefineLayoutChromeProps,
  type RefineLayoutConfig,
} from "./define-base-addon";
export {
  parseFlatSearch,
  stringifyFlatSearch,
} from "./search-codec";
export { PassthroughChrome } from "./route-tree";

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
 * `@angee/metadata` metadata, and any query needing different staleness (e.g.
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

  const i18n = createAngeeI18nRuntime(mergeI18n(enUiBundle, composed.i18n));

  // The static composition; the session fields (auth, logoutAction,
  // userPreferences) are layered in by RuntimeSessionProvider inside the frame.
  const runtime: Omit<AppRuntime, "auth" | "logoutAction" | "userPreferences"> = {
    widgets: { ...defaultWidgets, ...composed.widgets },
    i18n: i18n.instance,
    icons: composed.icons,
    forms: composed.forms,
    chatter: composed.chatter,
    chatterRoutes: chatterRouteIndex(routes, schemas),
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
  const refineI18nProvider = i18n.provider;
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
    resources: dataResourcesFromAngeeSchemaMetadata(schema.metadata),
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
  const logoutAction = useLogoutAction();
  return (
    <AuthStateProvider auth={auth}>
      <UserPreferencesProvider dataProviderName={authSchema}>
        <RuntimeSessionProvider auth={auth} logoutAction={logoutAction}>
          {children}
        </RuntimeSessionProvider>
      </UserPreferencesProvider>
    </AuthStateProvider>
  );
}

function RuntimeSessionProvider({
  auth,
  logoutAction,
  children,
}: {
  auth: AuthState;
  logoutAction: ReturnType<typeof useLogoutAction>;
  children: ReactNode;
}): ReactNode {
  const userPreferences = useUserPreferences();
  const runtime = useMemo<Partial<AppRuntime>>(
    () => ({ auth, logoutAction, userPreferences }),
    [auth, logoutAction, userPreferences],
  );
  return <AppRuntimeProvider runtime={runtime}>{children}</AppRuntimeProvider>;
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

function Redirect({ to }: { to: string }): ReactNode {
  const navigate = useNavigate();
  useEffect(() => {
    void navigate({ to });
  }, [to, navigate]);
  return null;
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

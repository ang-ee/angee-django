import {
  ResourceQuery,
  canonicalModelLabel,
  createAngeeAccessControlProvider,
  dataResourcesFromAngeeSchemaMetadata,
  defineAngeeSchemaMetadata,
  mergeModelLabelInventory,
  schemaFieldMetadataFromAngeeSchemaMetadata,
  type AngeeSchemaMetadata,
  type SchemaFieldMetadata,
} from "@angee/metadata";
import {
  type I18nResources,
  OperationDocumentsProvider,
  createAngeeHasuraDataProviders,
  createAngeeHasuraLiveProvider,
  createTanStackRouterProvider,
  viewAsAuth,
  type AngeeHasuraSchemaConfig,
  type SchemaOperationDocuments,
  type ResourceMutationOperations,
} from "@angee/refine";
import {
  Refine,
  useInvalidateAuthStore,
  type I18nProvider,
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
  redirect,
  useRouterState,
} from "@tanstack/react-router";
import {
  StrictMode,
  useCallback,
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
  applyDeveloperModeSearch,
  modelChain,
  useAppRuntime,
  useActiveRoute,
  DEFAULT_LOGIN_PATH,
  HOME_PATH_PREFERENCE_KEY,
  createRouteHref,
  type AppRuntime,
  type ComposedContainers,
  type RuntimeResourceRoutes,
  type RuntimeVocabulary,
} from "@angee/ui/runtime";
import { isBuiltInResourceViewKind, validateResourceViewPreset } from "@angee/ui/views/resource-view-model";
import { validateSearchShortcut } from "@angee/ui/views/resource-view-types";
import { composeAddons } from "./define-addon";
import {
  ModalsHost,
  ToastProvider,
  useRefineNotificationProvider,
} from "@angee/ui/feedback/index";
import { InAppLinkProvider, routerNavigator } from "@angee/ui/lib";
import { railDefaultTarget } from "@angee/ui/chrome/app-rail-model";
import { readAppRailPreferences } from "@angee/ui/chrome/app-rail-preferences";
import { baseIcons } from "@angee/ui/chrome/icon-registry";
import { ViewAsBanner } from "@angee/ui/chrome/ViewAs";
import { LoadingPanel } from "@angee/ui/fragments/index";
import {
  MenuTree,
  resolveMenuRouteTargets,
  type ChromeMenuItem,
} from "@angee/ui/chrome/menu-tree";
import { enUiBundle } from "@angee/ui/i18n";
import { defaultWidgets } from "@angee/ui/widgets/index";
import type { ThemeContribution } from "@angee/ui/theme";
import {
  APPEARANCE_CACHE_KEY,
  AppearanceProvider,
  appearanceCacheActorId,
  clearAppearanceCache,
  type HostAppearanceDefaults,
} from "@angee/ui/theme";
import { composeAppVocabulary } from "./providers/i18n";
import {
  type BaseAddon,
  type BaseAddonRoute,
  type BaseLayoutProvider,
  type RefineLayoutConfig,
} from "./define-base-addon";
import {
  AuthStateProvider,
  UserPreferencesProvider,
  createAngeeAuthProvider,
  identityQueryOptions,
  useLogoutAction,
  useRuntimeAuthState,
  useUserPreferences,
  type AuthState,
  type UserPreferences,
} from "./providers/auth";
import { createViewAsProvider, useViewAsState, type ViewAsProvider } from "./providers/view-as";
import {
  parseFlatSearch,
  stringifyFlatSearch,
} from "./search-codec";
import {
  refineResourcesForSchemas,
  refineRouteResourceProjection,
  AppRouteProjection,
  menuRouteResourceIdentifier,
  resourceMutationsForSchema,
} from "./resource-projection";
import { chatterRouteIndex } from "./chatter-routes";
import { explainComposition, type CompositionExplanation } from "./explain";
import { developmentMode } from "@angee/ui/lib/development-mode";
import { inheritedRouteFact, resolveRoutePaths } from "./route-paths";
import {
  compareCodePoint,
  authRouteError,
  createAddonRouteNodes,
  createLayoutRoutes,
  layoutNamesForRoutes,
  loadRouteIdentity,
} from "./route-tree";

export {
  dashboardPageRoute,
  defineBaseAddon,
  resourcePageRoutes,
  type BaseAddon,
  type BaseAddonRoute,
  type DashboardPageRouteOptions,
  type BaseLayoutProvider,
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
  /**
   * @deprecated Addons declare `shell.home`; a deployment pins it in `ANGEE_UI`.
   * When set, it overrides the composed shell's home.
   */
  home?: string;
  /**
   * @deprecated Addons declare a perspective and select it with `shell.perspective`.
   * When set, it overrides the composed perspective's menu root.
   */
  confineTo?: string;
  /** Auth-owned sign-in destination. Defaults to `/login`. */
  loginPath?: string;
  /** Build-owned defaults used until an authenticated user overrides them. */
  appearance?: HostAppearanceDefaults;
}

export type AngeeAppSchemaConfig =
  Omit<AngeeHasuraSchemaConfig, "metadata" | "mutations"> & {
    /** Generated schema metadata fetched from its emitted JSON asset. */
    metadata?: unknown;
    /** Generated operation documents imported from emitted project codegen. */
    operationDocuments?: SchemaOperationDocuments;
  };

type NormalizedAngeeAppSchemaConfig =
  Omit<AngeeAppSchemaConfig, "metadata"> & {
    metadata?: AngeeSchemaMetadata;
    mutations: Readonly<Record<string, ResourceMutationOperations>>;
    fieldMetadata: SchemaFieldMetadata;
  };

export interface AngeeApp {
  router: AnyRouter;
  mount(target: string | Element): Root;
  /** Which layer set the shell and each menu node, and why pages are hidden or unavailable. */
  explain: CompositionExplanation;
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
 * i18n · containers), owns the provider stack (GraphQL clients · runtime · live
 * invalidation · auth), builds the router, and mounts one persistent layout
 * route per refine layout. The host writes one
 * `createApp({...}).mount(...)`.
 */
export function createApp(input: CreateAppInput): AngeeApp {
  const queryClient = new QueryClient(APP_QUERY_CLIENT_CONFIG);
  const viewAs: ViewAsProvider = createViewAsProvider({
    changed: async (userId) => {
      // Stop live delivery before resetting/refetching any actor-bound data.
      refineLiveProvider?.setEnabled(false);
      resetSessionQueries(queryClient);
      if (userId === null) refineLiveProvider?.setEnabled(true);
      const identity = await queryClient.fetchQuery(identityQueryOptions(refineAuthProvider));
      if (userId !== null && (identity?.id !== userId || !identity.realUser)) {
        throw new Error("Preview identity was not returned.");
      }
    },
  });
  const schemas = normalizeSchemaConfigs(Object.fromEntries(
    Object.entries(input.schemas).map(([name, schema]) => [name, {
      ...schema,
      fetch: viewAsAuth(viewAs.getUserId)(schema.fetch ?? globalThis.fetch),
    }]),
  ));
  const modelLabelInventory = mergeModelLabelInventory(
    Object.values(schemas).map((schema) => schema.fieldMetadata),
  );
  const composed = composeAddons(
    [
      { id: "base", icons: baseIcons, layoutProviders: layoutNamesForRoutes(input.layouts)
        .filter((layout) => layout !== "console")
        .map((layout) => ({ id: "view-as", layout, component: ViewAsLayoutNotice })),
      },
      ...input.addons,
    ],
    {
      canonicalModelLabel: (spelling) =>
        canonicalModelLabel(modelLabelInventory, spelling),
    },
  );
  const routes = resolveRoutePaths(composed.routes as readonly BaseAddonRoute[]);
  for (const route of routes) {
    if (!route.recordMatch) continue;
    const model = route.recordModel ?? route.resource;
    const { field, equals } = route.recordMatch;
    if (!model || !field || !equals || !modelLabelInventory.some((resource) =>
      resource.modelLabel === model && Object.values(resource.query.fields).some((entry) => entry.row?.paths.includes(field)))) {
      throw new Error(`Route "${route.name}" has an unreadable record match "${field}" on "${model ?? "unknown"}".`);
    }
  }
  const routesByName = new Map(routes.map((route) => [route.name, route]));
  const routeDescriptors = routes.map(({ name, path }) => ({ name, path }));
  const routeHref = createRouteHref(routeDescriptors);
  const loginPath = input.loginPath ?? DEFAULT_LOGIN_PATH;
  const menus = resolveMenuRouteTargets(
    composed.menus as readonly ChromeMenuItem[],
    routeHref,
  );
  const menuTree = MenuTree.from(menus);
  const confineTo = input.confineTo ?? composed.shell.perspective?.root;
  if (confineTo !== undefined && !menuTree.roots.some((root) => root.id === confineTo)) {
    throw new Error(
      `Unknown menu root "${confineTo}": a perspective root must be a top-level menu item, not removed or included under another item.`,
    );
  }
  const homeInput = input.home ?? composed.shell.home;
  const projection = new AppRouteProjection(routes, menuTree, confineTo, {
    navigation: MenuTree.from(resolveMenuRouteTargets(composed.menuComposition.navigation, routeHref)),
    removed: composed.menuComposition.removed,
  });
  const unavailable = projection.unavailable;
  // Optional links (`maybe`, record destinations) skip unavailable pages; authored links still build.
  const runtimeRouteHref = unavailable.size
    ? createRouteHref(routeDescriptors, { unavailable: new Set(unavailable.keys()) })
    : routeHref;
  const navigationTree = projection.navigationTree;
  validateContainerConditions(composed.containers, {
    routes: routesByName,
    // The ids a page's app trail can hold: roots and included apps, flattened ones too.
    apps: menuTree.appIds(),
    perspectives: new Set(input.addons.flatMap((addon) => Object.keys(addon.perspectives ?? {}))),
  });
  for (const [address, children] of Object.entries(composed.containers.children)) {
    if (!address.endsWith("#search")) continue;
    const modelLabel = address.slice(0, -"#search".length);
    for (const child of children) {
      validateSearchShortcut(child.content, child.id);
      if (modelLabel === "resource") continue;
      const models = Object.values(schemas).flatMap((schema) => {
        const model = schema.fieldMetadata.labels[modelLabel];
        return model ? [model] : [];
      });
      let failure: unknown;
      const valid = models.some((model) => {
        try { validateSearchShortcut(child.content, child.id, ResourceQuery.from(model)); return true; }
        catch (error) { failure = error; return false; }
      });
      if (!valid) throw failure ?? new Error(`Unknown resource "${modelLabel}" in search shortcut "${child.id}".`);
    }
  }
  for (const preset of Object.values(composed.resourceViews)) {
    const models = Object.values(schemas).flatMap((schema) => {
      const model = schema.fieldMetadata.labels[preset.resource];
      return model ? [model] : [];
    });
    let failure: unknown;
    const valid = models.some((model) => {
      try { validateResourceViewPreset(preset, model); return true; }
      catch (error) { failure = error; return false; }
    });
    if (!valid) throw failure ?? new Error(`Unknown resource "${preset.resource}" in view "${preset.id}".`);
    // A preset may open on a contributed kind only where `resource#views` offers it for that resource.
    if (preset.view && !isBuiltInResourceViewKind(preset.view)) {
      const chain = modelChain(models[0]?.resource.canonicalLabel, preset.resource);
      const offered = ["resource#views", ...chain.map((model) => `${model}#views`)]
        .some((address) => composed.containers.children[address]?.some((child) => child.id === preset.view));
      if (!offered) throw new Error(`Resource view "${preset.id}" opens on view kind "${preset.view}", which no addon contributes to "${preset.resource}#views".`);
    }
  }
  const validateDefaultView = (id: string, route: BaseAddonRoute | undefined, menuId?: string) => {
    const preset = composed.resourceViews[id];
    const model = route ? inheritedRouteFact(route, routesByName, (item) => item.recordModel ?? item.resource) : undefined;
    if (!preset || preset.resource !== model) {
      throw new Error(menuId
        ? `Menu item "${menuId}" selects resource view "${id}" that route "${route?.name}" does not admit.`
        : `Unknown or incompatible default resource view "${id}" on route "${route?.name}".`);
    }
  };
  for (const route of routes) {
    if (route.defaultResourceView) validateDefaultView(route.defaultResourceView, route);
  }
  const menuPresetIdsByRoute = new Map<string, string[]>();
  for (const item of [...menuTree.byId.values()].sort((a, b) => a.id.localeCompare(b.id))) {
    if (!item.defaultResourceView) continue;
    const route = item.route ? routesByName.get(item.route) : undefined;
    if (!route) {
      throw new Error(`Menu item "${item.id}" selects resource view "${item.defaultResourceView}" without a target route.`);
    }
    validateDefaultView(item.defaultResourceView, route, item.id);
    const admitted = menuPresetIdsByRoute.get(route.name) ?? [];
    admitted.push(item.defaultResourceView);
    menuPresetIdsByRoute.set(route.name, admitted);
  }
  const routesByResource = projection.resourceRoutes(confineTo);

  const defaultSchema = input.defaultSchema ?? "public";
  const subscriptionSchema = input.subscriptionSchema ?? "console";
  const vocabularyForRoute = composeAppVocabulary(
    mergeI18n(enUiBundle, composed.i18n), composed.vocabulary,
    modelLabelInventory, menuTree, routes,
  );
  const defaultVocabulary = vocabularyForRoute(confineTo);
  const i18n = defaultVocabulary.i18n;

  // The static composition; the session fields (auth, logoutAction,
  // userPreferences) are layered in by RuntimeSessionProvider inside the frame.
  const runtime: Omit<AppRuntime, "auth" | "logoutAction" | "userPreferences"> = {
    confineTo: confineTo ?? null,
    brand: composed.brand,
    widgets: { ...defaultWidgets, ...composed.widgets },
    statusTones: composed.statusTones,
    i18n: i18n.instance,
    vocabulary: defaultVocabulary.vocabulary,
    resourceViews: composed.resourceViews,
    icons: composed.icons,
    forms: composed.forms,
    chatterRoutes: chatterRouteIndex(routes, modelLabelInventory),
    recordSearchKeys: composed.recordSearchKeys,
    // Built-in renderers are universal (PreviewPane always includes them); the
    // runtime carries only addon-contributed providers.
    previews: composed.previews,
    dashboards: composed.dashboards,
    routesByResource,
    routeHref: runtimeRouteHref,
    loginPath,
    themes: composed.themes as readonly ThemeContribution[],
    containers: composed.containers,
  };
  const routeTrail = (route: BaseAddonRoute | undefined): string[] => {
    const names: string[] = [];
    for (let current = route; current && !names.includes(current.name); current = current.parent ? routesByName.get(current.parent) : undefined) {
      names.push(current.name);
    }
    return names;
  };
  const operationDocuments = operationDocumentsForSchemas(schemas);
  function resourceRegistryFor(
    selected: Readonly<Record<string, RuntimeResourceRoutes>>,
    vocabulary: RuntimeVocabulary,
  ) {
    const paths = Object.fromEntries(Object.entries(selected).map(([resource, names]) => [resource, routeHref(names.collection)]));
    const projected = refineRouteResourceProjection(routes, menuTree, navigationTree, selected);
    return [...projected.resources, ...refineResourcesForSchemas(schemas, paths, projected.metadataByResource)]
      .map((resource) => {
        const model = resource.meta?.modelLabel;
        const words = typeof model === "string" ? vocabulary.resources[model] : undefined;
        const menuId = resource.meta?.menuId;
        const label = typeof menuId === "string" ? vocabulary.menus[menuId] : words?.pluralLabel ?? words?.label;
        return label === undefined ? resource : { ...resource, meta: { ...resource.meta, label } };
      });
  }
  const refineResourceRegistry = resourceRegistryFor(routesByResource, runtime.vocabulary);
  const refineDataProviders = mergeAddonDataProviders(
    createAngeeHasuraDataProviders(schemas, defaultSchema),
    composed.dataProviders as Readonly<
      Record<string, Required<RefineDataProvider>>
    >,
  );
  // The one QueryClient instance createApp owns (per `@angee/app` `index.ts`):
  // shared by `<Refine>` and the route gate so identity is fetched once.
  const refineLiveProvider = createLiveProviderForSchema(
    schemas,
    subscriptionSchema,
    queryClient,
  );
  const authSchema = authSchemaNameForSchemas(schemas, defaultSchema);
  const refineAuthProvider: RefineAuthProvider = createAuthProviderForSchema(
    schemas,
    authSchema,
    loginPath,
    queryClient,
    () => {
      viewAs.reset();
      refineLiveProvider?.setEnabled(true);
    },
  );
  const refineAccessControlProvider = createAngeeAccessControlProvider(
    refineResourceRegistry,
  );
  const home =
    (homeInput ? homeInput.startsWith("/") ? homeInput : routeHref(homeInput) : undefined) ??
    (confineTo !== undefined ? navigationTree.roots[0]?.target : undefined) ??
    routes.find((route) => route.layout !== "public" && !unavailable.has(route.name))?.path ??
    "/";
  const homePath = new URL(home, "https://angee.invalid").pathname;
  const homeRoute = homeInput && !homeInput.startsWith("/")
    ? routesByName.get(homeInput) : routes.find((route) => route.path === homePath);
  if (homeRoute && unavailable.has(homeRoute.name)) {
    throw new Error(`Home "${home}" is unavailable: ${unavailable.get(homeRoute.name)}.`);
  }
  if (confineTo !== undefined && (homePath === "/"
    || !(homeRoute ? projection.rootFor(homeRoute) === confineTo : menuTree.activeAppRoot(homePath)?.id === confineTo))) {
    throw new Error(`Home "${home}" must belong to confined menu root "${confineTo}".`);
  }

  function RootOutlet(): ReactNode {
    const pathname = useRouterState({ select: (state) => state.location.pathname });
    const searchStr = useRouterState({ select: (state) => state.location.searchStr });
    const activeRoute = useActiveRoute(routes);
    const match = projection.activeMenu(pathname, activeRoute?.name, searchStr);
    const activeMenuId = match?.item.id ?? null;
    const app = projection.activeApp(pathname, activeRoute?.name, searchStr);
    const words = vocabularyForRoute(app, activeRoute?.name);
    const publicRoute = activeRoute?.layout === "public"
      || pathname.replace(/\/$/, "") === loginPath.replace(/\/$/, "");
    // Public routes and sign-in sit outside every app, so app-scoped narrowing never reaches them.
    const appTrail = publicRoute ? "" : projection.appTrail(pathname, activeRoute?.name, searchStr).join("\0");
    const scopedRuntime = useMemo(() => {
      const selected = projection.resourceRoutes(app, activeRoute?.name);
      const menuResourceViewIds = new Set<string>();
      let route = activeRoute;
      while (route) {
        for (const id of menuPresetIdsByRoute.get(route.name) ?? []) menuResourceViewIds.add(id);
        route = route.parent ? routesByName.get(route.parent) : undefined;
      }
      return {
        ...runtime,
        i18n: words.i18n.instance,
        vocabulary: words.vocabulary,
        defaultResourceView: projection.defaultResourceView(activeRoute?.name),
        menuResourceViewIds: [...menuResourceViewIds].sort(),
        routesByResource: selected,
        routeHref: runtimeRouteHref,
        composition: explain,
        containerScope: {
          apps: appTrail ? appTrail.split("\0") : [],
          routes: routeTrail(activeRoute),
          perspective: confineTo !== undefined ? composed.shell.perspective?.id ?? null : null,
        },
        activeRouteName: activeRoute?.name ?? null,
        activeMenuId,
        activeApp: app ?? null,
      };
    }, [app, appTrail, activeRoute, activeMenuId, words]);
    return (
      <NuqsAdapter>
        <OperationDocumentsProvider documents={operationDocuments}>
          <AppRuntimeProvider runtime={scopedRuntime}>
            <InAppLinkProvider navigate={navigateInApp}>
              <ModalsHost>
                <ToastProvider>
                  <RefineRoot i18nProvider={words.i18n.provider} />
                </ToastProvider>
              </ModalsHost>
            </InAppLinkProvider>
          </AppRuntimeProvider>
        </OperationDocumentsProvider>
      </NuqsAdapter>
    );
  }

  function RefineRoot({ i18nProvider }: { i18nProvider: I18nProvider }): ReactNode {
    const { vocabulary, routesByResource: selected, activeMenuId } = useAppRuntime();
    const labeledResources = useMemo(() => resourceRegistryFor(selected, vocabulary), [selected, vocabulary]);
    const refineNotificationProvider = useRefineNotificationProvider();
    const routerProvider = useMemo(() => createTanStackRouterProvider(activeMenuId ? menuRouteResourceIdentifier(activeMenuId) : undefined), [activeMenuId]);
    return (
      <Refine
        authProvider={refineAuthProvider}
        accessControlProvider={refineAccessControlProvider}
        dataProvider={refineDataProviders}
        i18nProvider={i18nProvider}
        liveProvider={refineLiveProvider}
        notificationProvider={refineNotificationProvider}
        resources={labeledResources}
        routerProvider={routerProvider}
        options={{
          liveMode: refineLiveProvider ? "auto" : "off",
          syncWithLocation: false,
          reactQuery: { clientConfig: queryClient },
        }}
      >
        <AppFrame
          viewAs={viewAs}
          authSchema={authSchema}
          loginPath={loginPath}
          appearance={input.appearance}
        >
          <Outlet />
        </AppFrame>
      </Refine>
    );
  }

  const rootRoute = createRootRoute({
    component: RootOutlet,
    // `?debug=1|0` sets developer mode for the session on any navigation, `/` included.
    beforeLoad: ({ search }) => applyDeveloperModeSearch((search as Record<string, unknown>).debug),
  });

  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    beforeLoad: async () => {
      const identity = await loadRouteIdentity(refineAuthProvider, queryClient);
      throw redirect({
        href: homeTarget(home, confineTo !== undefined, navigationTree, identity?.preferences ?? {}),
        replace: true,
      });
    },
    errorComponent: authRouteError(queryClient, refineAuthProvider),
  });

  const layoutRoutes = createLayoutRoutes({
    rootRoute,
    layoutNames: layoutNamesForRoutes(input.layouts),
    layouts: input.layouts,
    schemas,
    defaultSchema,
    authProvider: refineAuthProvider,
    queryClient,
    loginPath,
    layoutProviders: composed.layoutProviders as readonly BaseLayoutProvider[],
  });
  createAddonRouteNodes({
    routes,
    routesByName,
    layoutRoutes,
    ...(confineTo !== undefined || unavailable.size
      ? { consoleConfinement: { allows: (route, pathname) => projection.allows(route, pathname), home } }
      : {}),
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
  // Bound after the router exists; RootOutlet only reads it at render time.
  const navigateInApp = routerNavigator(router);
  const explain = explainComposition(composed.shell, composed.menuComposition, unavailable, {
    home,
    confineTo: confineTo ?? null,
  }, composed.containers);
  if (developmentMode()) {
    for (const diagnostic of composed.shell.diagnostics) console.warn(`[angee] ${diagnostic}`);
    const menuFindings = composed.menuComposition.diagnostics.length;
    if (menuFindings) console.warn(`[angee] ${menuFindings} menu finding(s); see createApp(...).explain.menus.diagnostics.`);
  }
  return {
    router,
    explain,
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
    mutations: resourceMutationsForSchema(normalizedMetadata),
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
  queryClient: QueryClient,
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
    queryClient,
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
  loginPath: string,
  queryClient: QueryClient,
  onAuthChange: () => void,
): RefineAuthProvider {
  const schema = schemas[authSchema];
  if (!schema) {
    throw new Error(`No GraphQL schema config for auth schema "${authSchema}".`);
  }
  return createAngeeAuthProvider({
    ...schema,
    loginPath,
    queryClient,
    identityClient: schemas.console,
    // Reset observed queries so identity and mounted views see the transition;
    // clearing their entries would strand observers with the previous data.
    onAuthChange: () => {
      onAuthChange();
      resetSessionQueries(queryClient);
    },
  });
}

/** Preserve native observers while discarding data from the previous actor. */
function resetSessionQueries(queryClient: QueryClient): void {
  queryClient.removeQueries({ predicate: (query) => query.getObserversCount() === 0 });
  queryClient.getMutationCache().clear();
  void queryClient.resetQueries();
}

/**
 * The provider frame inside the client pool: resolve the current actor, open the
 * change subscriptions on the subscription schema's client, and expose the
 * runtime and auth state to every route.
 */
function AppFrame({
  viewAs,
  authSchema,
  loginPath,
  appearance,
  children,
}: {
  viewAs: ViewAsProvider;
  authSchema: string;
  loginPath: string;
  appearance?: HostAppearanceDefaults;
  children: ReactNode;
}): ReactNode {
  const { auth: identityAuth, identity } = useRuntimeAuthState();
  const preview = useViewAsState(viewAs);
  const auth = useMemo<AuthState>(() => ({
    ...identityAuth,
    viewAs: {
      ...preview,
      currentUser: preview.pending ? null : identity,
      realUser: identity?.realUser ?? null,
      viewablePeople: identity?.viewablePeople ?? [],
      enter: (userId) => { void viewAs.enter(userId); },
      exit: () => { void viewAs.exit(); },
    },
  }), [identityAuth, identity, preview, viewAs]);
  const invalidateAuthStore = useInvalidateAuthStore();
  const sourceLogoutAction = useLogoutAction();
  const actorId = auth.status === "resolving" ? null : auth.user?.id ?? "anonymous";
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onStorage = (event: StorageEvent) => {
      if (event.key !== APPEARANCE_CACHE_KEY) return;
      const nextActorId = appearanceCacheActorId(event.newValue);
      if (event.newValue === null || (actorId !== null && nextActorId !== null && nextActorId !== actorId)) {
        void invalidateAuthStore();
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [actorId, invalidateAuthStore]);
  const logout = useCallback(async () => {
    if (viewAs.getSnapshot().pending) return false;
    await viewAs.exit();
    const success = await sourceLogoutAction.logout();
    if (success) clearAppearanceCache();
    return success;
  }, [sourceLogoutAction.logout, viewAs]);
  const logoutAction = useMemo(() => ({ ...sourceLogoutAction, logout }), [logout, sourceLogoutAction]);
  return (
    <AuthStateProvider auth={auth}>
      <UserPreferencesProvider dataProviderName={authSchema}>
        <RuntimeSessionProvider
          auth={auth}
          logoutAction={logoutAction}
          loginPath={loginPath}
          appearance={appearance}
        >
          {children}
        </RuntimeSessionProvider>
      </UserPreferencesProvider>
    </AuthStateProvider>
  );
}

function RuntimeSessionProvider({
  auth,
  logoutAction,
  loginPath,
  appearance,
  children,
}: {
  auth: AuthState;
  logoutAction: ReturnType<typeof useLogoutAction>;
  loginPath: string;
  appearance?: HostAppearanceDefaults;
  children: ReactNode;
}): ReactNode {
  const userPreferences = useUserPreferences();
  const runtime = useMemo<Partial<AppRuntime>>(
    () => ({ auth, logoutAction, userPreferences, loginPath }),
    [auth, loginPath, logoutAction, userPreferences],
  );
  return (
    <AppRuntimeProvider runtime={runtime}>
      <AppearanceProvider host={appearance}>{children}</AppearanceProvider>
    </AppRuntimeProvider>
  );
}

/** Public and other layouts retain an exit path when leaving console chrome. */
function ViewAsLayoutNotice({ children }: { children: ReactNode }): ReactNode {
  return <><ViewAsBanner />{children}</>;
}

/** Resolve the home preference against the composed navigation, before a route commits. */
function homeTarget(fallback: string, confined: boolean, menuTree: MenuTree, preferences: UserPreferences): string {
  if (confined) return fallback;
  const preferredPath = preferences[HOME_PATH_PREFERENCE_KEY];
  if (typeof preferredPath === "string" && preferredPath.startsWith("/")) {
    return preferredPath;
  }
  const defaultItemId = readAppRailPreferences(preferences).defaultItemId;
  const item = menuTree.railMenuItems().find((node) => node.id === defaultItemId);
  return (item && railDefaultTarget(item)) ?? fallback;
}

/** Base and addon namespaces have disjoint ownership. */
function mergeI18n(base: I18nResources, addons: I18nResources): I18nResources {
  for (const namespace of Object.keys(addons)) {
    if (Object.prototype.hasOwnProperty.call(base, namespace)) {
      throw new Error(`Addon i18n namespace "${namespace}" is owned by the base bundle.`);
    }
  }
  return { ...base, ...addons };
}

/**
 * A container condition names a route, an app (a root or an included app, flattened
 * ones too: what a page's app trail holds) or a perspective
 * that exists; a misspelt one would never match, so it fails at boot.
 */
function validateContainerConditions(
  containers: ComposedContainers,
  known: { routes: ReadonlyMap<string, unknown>; apps: ReadonlySet<string>; perspectives: ReadonlySet<string> },
): void {
  const listed = (value: string | readonly string[] | undefined): readonly string[] =>
    value === undefined ? [] : typeof value === "string" ? [value] : value;
  for (const [address, rules] of Object.entries(containers.rules)) {
    for (const rule of rules) {
      const where = `Addon "${rule.layer}" narrows "${address}"`;
      for (const route of listed(rule.when?.route)) {
        if (!known.routes.has(route)) throw new Error(`${where} on unknown route "${route}".`);
      }
      for (const app of listed(rule.when?.app)) {
        if (!known.apps.has(app)) throw new Error(`${where} in unknown app "${app}".`);
      }
      for (const perspective of listed(rule.when?.perspective)) {
        if (!known.perspectives.has(perspective)) throw new Error(`${where} in unknown perspective "${perspective}".`);
      }
    }
  }
}

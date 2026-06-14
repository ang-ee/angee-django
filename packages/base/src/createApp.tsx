import {
  StrictMode,
  useEffect,
  type ComponentType,
  type ReactNode,
} from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  type AnyRoute,
  type AnyRouter,
  Outlet,
  RouterProvider,
  type StaticDataRouteOption,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  useNavigate,
} from "@tanstack/react-router";
import { NuqsAdapter } from "nuqs/adapters/tanstack-router";
import {
  AppRuntimeProvider,
  AuthProvider,
  GraphQLClientProvider,
  GraphQLProvider,
  RelayInvalidationProvider,
  changeSubscriptionFields,
  composeAddons,
  mergeSlotContributions,
  useRuntimeAuthState,
  useSchemaClients,
  type AddonManifest,
  type AddonRoute,
  type AngeeUrqlClientOptions,
  type AppRuntime,
  type ComposedMenuItem,
  type I18nResources,
  type SlotContribution,
} from "@angee/sdk";

import { ModalsHost, ToastProvider } from "./feedback";
import { baseIcons } from "./chrome/icon-registry";
import {
  MenuTree,
  type BaseMenuItem,
  type ChromeMenuItem,
  type ChromeMenuNode,
} from "./chrome/menu-tree";
import { enBaseBundle, useBaseT } from "./i18n";
import {
  useRouteChrome,
  type BreadcrumbItem,
  type RouteBreadcrumbFactory,
  type RouteChromeStaticData,
} from "./route-static-data";
import { defaultWidgets } from "./widgets";

/** A route that also carries the page component the chrome renders. */
export interface BaseAddonRoute extends AddonRoute {
  component?: ComponentType;
  /** Dynamic crumb factory for this route's match. */
  crumb?: RouteBreadcrumbFactory;
  /**
   * Menu item id whose trail seeds chrome for routes outside the menu, or
   * disambiguates chrome derivation when multiple menu items target this route.
   */
  menu?: string;
  title?: ReactNode;
  icon?: string;
  breadcrumbs?: readonly BreadcrumbItem[];
}

/** An addon manifest whose routes carry their page components. */
export interface BaseAddon extends Omit<AddonManifest, "routes" | "menus"> {
  routes?: readonly BaseAddonRoute[];
  menus?: readonly BaseMenuItem[];
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

/** Props passed from the active route into a shell chrome component. */
export interface ShellChromeProps
  extends Pick<RouteChromeStaticData, "icon" | "title"> {
  children: ReactNode;
}

/** A shell registered with `createApp`: the chrome component + auth policy. */
export interface ShellConfig {
  /** Chrome wrapping route bodies (e.g. `ConsoleShell`). */
  chrome?: ComponentType<ShellChromeProps>;
  /** Gate routes behind sign-in. Defaults to `true` for every shell but `public`. */
  requireAuth?: boolean;
  /**
   * GraphQL schema this shell's routes read from; reads inside the shell inherit
   * that client. Defaults to the app's `defaultSchema`, so the common console
   * surface needs no override and only the public shell pins itself to `public`.
   */
  schema?: string;
}

export interface CreateAppInput {
  addons: readonly BaseAddon[];
  shells: Record<string, ShellConfig>;
  /** One client config per named schema (url, ws endpoint, cache). */
  schemas: Record<string, AngeeUrqlClientOptions>;
  /** Schema bound to the app subtree's reads. Defaults to `public`. */
  defaultSchema?: string;
  /** Schema carrying the change subscriptions. Defaults to `console`. */
  subscriptionSchema?: string;
  /** Where `/` redirects. Defaults to the first non-public route's path. */
  home?: string;
  /** Host-level UI slot contributions, merged with the addons'. */
  slots?: readonly SlotContribution[];
}

export interface AngeeApp {
  router: AnyRouter;
  mount(target: string | Element): Root;
}

/**
 * `createApp` — the single composition root. It merges the addon manifests into
 * one runtime (routes · route-resolved menus · derived route chrome · widgets ·
 * i18n · slots), owns the provider stack (GraphQL clients · runtime · live
 * invalidation · auth), builds the router, and mounts one persistent layout
 * route per shell. The host writes one
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
  validateRoutes(routes, routesByName, input.shells);
  const menus = resolveMenuRouteTargets(
    composed.menus as readonly ChromeMenuItem[],
    pathByName,
  );
  const menuTree = MenuTree.from(menus);

  const routeChromeByName = routeChromeByNameFromMenu(routes, menuTree);

  const runtime: AppRuntime = {
    widgets: { ...defaultWidgets, ...composed.widgets },
    menus,
    // Seed the base namespace under the merged addon bundles; an addon key wins.
    i18n: mergeI18n(enBaseBundle, composed.i18n),
    icons: composed.icons,
    forms: composed.forms,
    chatter: composed.chatter,
    slots: mergeSlotContributions(composed.slots, input.slots ?? []),
    // Built-in renderers are universal (PreviewPane always includes them); the
    // runtime carries only addon-contributed providers.
    previews: composed.previews,
  };

  const defaultSchema = input.defaultSchema ?? "public";
  const subscriptionSchema = input.subscriptionSchema ?? "console";
  // Only the models the subscription schema actually publishes a change event for
  // get a live subscription; blind-subscribing an undefined `<model>Changed` errors
  // server-side. Absent an SDL we keep the prior behaviour (subscribe to all).
  const subscriptionSdl = input.schemas[subscriptionSchema]?.sdl;
  const availableChangeFields = subscriptionSdl
    ? changeSubscriptionFields(subscriptionSdl)
    : undefined;
  const home =
    resolvePath(input.home, pathByName) ??
    routes.find((route) => route.shell !== "public")?.path ??
    "/";

  const rootRoute = createRootRoute({ component: RootOutlet });

  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <Redirect to={home} />,
  });

  const shellRoutes = createShellRoutes({
    rootRoute,
    shellNames: shellNamesForRoutes(input.shells),
    shells: input.shells,
    defaultSchema,
  });
  createAddonRouteNodes({
    routes,
    routesByName,
    routeChromeByName,
    shellRoutes,
  });

  const router = createRouter({
    routeTree: rootRoute.addChildren([
      indexRoute,
      ...[...shellRoutes.entries()]
        .sort(([left], [right]) => compareCodePoint(left, right))
        .map(([, route]) => route),
    ]),
    history: typeof window === "undefined" ? createMemoryHistory() : undefined,
    parseSearch: parseFlatSearch,
    stringifySearch: stringifyFlatSearch,
    defaultPreload: false,
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
          <GraphQLClientProvider config={input.schemas} schema={defaultSchema}>
            <AppFrame
              runtime={runtime}
              subscriptionSchema={subscriptionSchema}
              availableChangeFields={availableChangeFields}
            >
              <RouterProvider router={router} />
            </AppFrame>
          </GraphQLClientProvider>
        </StrictMode>,
      );
      return root;
    },
  };
}

// Keep search values flat and unquoted so login next round-trips raw and
// data-view values read like status:year.
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
  runtime,
  subscriptionSchema,
  availableChangeFields,
  children,
}: {
  runtime: AppRuntime;
  subscriptionSchema: string;
  availableChangeFields?: ReadonlySet<string>;
  children: ReactNode;
}): ReactNode {
  const { auth } = useRuntimeAuthState();
  const clients = useSchemaClients();
  return (
    <AppRuntimeProvider runtime={runtime}>
      <ModalsHost>
        <ToastProvider>
          <RelayInvalidationProvider
            client={clients[subscriptionSchema]}
            availableChangeFields={availableChangeFields}
          >
            <AuthProvider auth={auth}>{children}</AuthProvider>
          </RelayInvalidationProvider>
        </ToastProvider>
      </ModalsHost>
    </AppRuntimeProvider>
  );
}

function RootOutlet(): ReactNode {
  return (
    <NuqsAdapter>
      <Outlet />
    </NuqsAdapter>
  );
}

function ShellLayoutRoute({
  shellName,
  shells,
  defaultSchema,
}: {
  shellName: string;
  shells: Record<string, ShellConfig>;
  defaultSchema: string;
}): ReactNode {
  const shell = shells[shellName];
  const clients = useSchemaClients();
  const Chrome = shell?.chrome ?? PassthroughChrome;
  const { icon, title } = useRouteChrome();
  const requireAuth = shell?.requireAuth ?? shellName !== "public";
  const body = (
    <Chrome icon={icon} title={title}>
      <Outlet />
    </Chrome>
  );
  const gated = requireAuth ? <RequireAuth>{body}</RequireAuth> : body;
  // Bind the route to its shell's schema client, so reads inside inherit it.
  return (
    <GraphQLProvider clients={clients} schema={shell?.schema ?? defaultSchema}>
      {gated}
    </GraphQLProvider>
  );
}

function createShellRoutes({
  rootRoute,
  shellNames,
  shells,
  defaultSchema,
}: {
  rootRoute: AnyRoute;
  shellNames: readonly string[];
  shells: Record<string, ShellConfig>;
  defaultSchema: string;
}): Map<string, AnyRoute> {
  const shellRoutes = new Map<string, AnyRoute>();
  for (const shellName of shellNames) {
    shellRoutes.set(
      shellName,
      createRoute({
        getParentRoute: () => rootRoute,
        id: shellLayoutRouteId(shellName),
        component: () => (
          <ShellLayoutRoute
            shellName={shellName}
            shells={shells}
            defaultSchema={defaultSchema}
          />
        ),
      }),
    );
  }
  return shellRoutes;
}

function createAddonRouteNodes({
  routes,
  routesByName,
  routeChromeByName,
  shellRoutes,
}: {
  routes: readonly BaseAddonRoute[];
  routesByName: ReadonlyMap<string, BaseAddonRoute>;
  routeChromeByName: ReadonlyMap<string, RouteChromeStaticData>;
  shellRoutes: ReadonlyMap<string, AnyRoute>;
}): void {
  const routeNodes = new Map<string, AnyRoute>();
  const childrenByParent = new Map<AnyRoute, Array<NamedRouteNode>>();

  const buildRoute = (route: BaseAddonRoute): AnyRoute => {
    const existing = routeNodes.get(route.name);
    if (existing) return existing;
    const parentManifestRoute = route.parent
      ? routesByName.get(route.parent)
      : undefined;
    const parentNode = parentManifestRoute
      ? buildRoute(parentManifestRoute)
      : shellRoutes.get(route.shell)!;
    const node = createAddonRouteNode(
      route,
      parentNode,
      parentManifestRoute,
      routeChromeByName.get(route.name),
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

function createAddonRouteNode(
  route: BaseAddonRoute,
  parentNode: AnyRoute,
  parentManifestRoute: BaseAddonRoute | undefined,
  chrome: RouteChromeStaticData | undefined,
): AnyRoute {
  const Page = route.component;
  return createRoute({
    getParentRoute: () => parentNode,
    path: routePathUnderParent(route, parentManifestRoute),
    staticData: routeStaticData(route, chrome),
    ...(Page ? { component: () => <Page /> } : {}),
  });
}

interface NamedRouteNode {
  name: string;
  route: AnyRoute;
}

function routeStaticData(
  route: BaseAddonRoute,
  chrome: RouteChromeStaticData | undefined,
): StaticDataRouteOption {
  return {
    ...(chrome ? { chrome } : {}),
    ...(route.crumb ? { breadcrumb: route.crumb } : {}),
  };
}

function shellNamesForRoutes(shells: Record<string, ShellConfig>): readonly string[] {
  return Object.keys(shells).sort(compareCodePoint);
}

function shellLayoutRouteId(shellName: string): string {
  return `_angee_shell_${shellName}`;
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

/** Gate a subtree behind sign-in; bounce to `/login` while or after resolving. */
function RequireAuth({ children }: { children: ReactNode }): ReactNode {
  const t = useBaseT();
  const { auth, fetching } = useRuntimeAuthState();
  const navigate = useNavigate();
  const signedOut = !fetching && !auth.user;
  useEffect(() => {
    if (!signedOut) return;
    const next =
      typeof window !== "undefined"
        ? `${window.location.pathname}${window.location.search}`
        : "/";
    void navigate({ to: "/login", search: { next } });
  }, [signedOut, navigate]);
  if (fetching && !auth.user) return <FullPageStatus message={t("app.loadingWorkspace")} />;
  if (!auth.user) return <FullPageStatus message={t("app.redirectingSignIn")} />;
  return <>{children}</>;
}

function Redirect({ to }: { to: string }): ReactNode {
  const navigate = useNavigate();
  useEffect(() => {
    void navigate({ to });
  }, [to, navigate]);
  return null;
}

function FullPageStatus({ message }: { message: string }): ReactNode {
  return (
    <main className="grid min-h-screen place-content-center bg-canvas text-sm text-fg-muted">
      {message}
    </main>
  );
}

export function PassthroughChrome({ children }: ShellChromeProps): ReactNode {
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

function routeChromeByNameFromMenu(
  routes: readonly BaseAddonRoute[],
  menuTree: MenuTree,
): Map<string, RouteChromeStaticData> {
  const routeChromeByName = new Map<string, RouteChromeStaticData>();
  for (const route of routes) {
    const refs = menuTree.itemsForRoute(route.name);
    const fields = chromeFieldsNeedingDerivation(route);
    const selected = selectedMenuItemForRoute(route, refs, menuTree, fields);
    const derived = selected
      ? chromePropsFromTrail(
          menuTree.trailFor(selected.id),
          selected.route !== route.name,
        )
      : {};
    const chrome = materializeRouteChrome({
      title: route.title ?? derived.title,
      icon: route.icon ?? derived.icon,
      breadcrumbs: route.breadcrumbs ?? derived.breadcrumbs,
    });
    if (hasRouteChrome(chrome)) routeChromeByName.set(route.name, chrome);
  }
  return routeChromeByName;
}

function selectedMenuItemForRoute(
  route: BaseAddonRoute,
  refs: readonly ChromeMenuNode[],
  menuTree: MenuTree,
  fields: readonly ChromeField[],
): ChromeMenuNode | undefined {
  if (route.menu) {
    const selected = menuTree.byId.get(route.menu);
    if (!selected) {
      throw new Error(
        `Route "${route.name}" references unknown menu item "${route.menu}".`,
      );
    }
    if (refs.length > 0 && !refs.some((item) => item.id === selected.id)) {
      throw new Error(
        `Route "${route.name}" sets menu "${route.menu}", but that item does not reference the route.`,
      );
    }
    return selected;
  }
  if (refs.length === 1) return refs[0];
  if (refs.length > 1 && fields.length > 0) {
    throw new Error(
      `Route "${route.name}" is referenced by multiple menu items; declare route.menu or explicit chrome for ${fields.join(", ")}.`,
    );
  }
  return undefined;
}

function chromePropsFromTrail(
  trail: readonly ChromeMenuNode[],
  linkLeaf: boolean,
): RouteChromeStaticData {
  const root = trail[0];
  return {
    title: root?.displayLabel,
    icon: root?.iconName,
    breadcrumbs: trail.map((item, index) => {
      const leaf = index === trail.length - 1;
      return {
        label: item.displayLabel,
        to: !leaf || linkLeaf ? item.target : undefined,
      };
    }),
  };
}

function hasRouteChrome(chrome: RouteChromeStaticData): boolean {
  return (
    chrome.title !== undefined ||
    chrome.icon !== undefined ||
    chrome.breadcrumbs !== undefined
  );
}

function materializeRouteChrome(
  chrome: RouteChromeStaticData,
): RouteChromeStaticData {
  if (chrome.breadcrumbs !== undefined || chrome.title === undefined) {
    return chrome;
  }
  return { ...chrome, breadcrumbs: [{ label: chrome.title }] };
}

type ChromeField = "title" | "icon" | "breadcrumbs";

const DERIVED_CHROME_FIELDS: readonly ChromeField[] = [
  "title",
  "icon",
  "breadcrumbs",
];

// Each chrome field has independent precedence: explicit route field first,
// otherwise derive that field from the selected menu trail when one is needed.
function chromeFieldsNeedingDerivation(
  route: BaseAddonRoute,
): readonly ChromeField[] {
  return DERIVED_CHROME_FIELDS.filter((field) => route[field] === undefined);
}

function validateRoutes(
  routes: readonly BaseAddonRoute[],
  routesByName: ReadonlyMap<string, BaseAddonRoute>,
  shells: Record<string, ShellConfig>,
): void {
  for (const route of routes) {
    if (!Object.prototype.hasOwnProperty.call(shells, route.shell)) {
      throw new Error(
        `Route "${route.name}" references undeclared shell "${route.shell}".`,
      );
    }
    if (!route.component && !route.parent) {
      throw new Error(
        `Route "${route.name}" must declare component unless it is nested under parent.`,
      );
    }
    const parent = route.parent ? routesByName.get(route.parent) : undefined;
    if (route.parent && !parent) {
      throw new Error(
        `Route "${route.name}" references unknown parent route "${route.parent}".`,
      );
    }
    if (!parent) continue;
    if (parent.shell !== route.shell) {
      throw new Error(
        `Route "${route.name}" parent "${parent.name}" must use the same shell.`,
      );
    }
    if (!isProperPathPrefix(parent.path, route.path)) {
      throw new Error(
        `Route "${route.name}" path "${route.path}" must be nested under parent "${parent.name}" path "${parent.path}".`,
      );
    }
  }
}

function routePathUnderParent(
  route: BaseAddonRoute,
  parent: BaseAddonRoute | undefined,
): string {
  if (!parent) return route.path;
  const parentPath = normalizeRoutePath(parent.path);
  const childPath = normalizeRoutePath(route.path);
  if (parentPath === "/") return childPath.slice(1);
  return childPath.slice(parentPath.length + 1);
}

function isProperPathPrefix(parentPath: string, childPath: string): boolean {
  const parent = normalizeRoutePath(parentPath);
  const child = normalizeRoutePath(childPath);
  if (parent === child) return false;
  if (parent === "/") return child.startsWith("/");
  return child.startsWith(`${parent}/`);
}

function normalizeRoutePath(path: string): string {
  if (path === "/") return "/";
  const withLeadingSlash = path.startsWith("/") ? path : `/${path}`;
  return withLeadingSlash.replace(/\/+$/, "");
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

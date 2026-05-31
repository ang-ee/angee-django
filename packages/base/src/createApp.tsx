import {
  StrictMode,
  useEffect,
  useMemo,
  type ComponentType,
  type ReactNode,
} from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  Outlet,
  RouterProvider,
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
  RelayInvalidationProvider,
  composeAddons,
  mergeSlotContributions,
  useRuntimeAuthState,
  useSchemaClients,
  type AddonManifest,
  type AddonRoute,
  type AngeeUrqlClientOptions,
  type AppRuntime,
  type I18nResources,
  type SlotContribution,
} from "@angee/sdk";

import { ModalsHost } from "./feedback";
import { enBaseBundle } from "./i18n";
import { defaultWidgets } from "./widgets";

/** A route that also carries the page component the chrome renders. */
export interface BaseAddonRoute extends AddonRoute {
  component: ComponentType;
}

/** An addon manifest whose routes carry their page components. */
export interface BaseAddon extends Omit<AddonManifest, "routes"> {
  routes?: readonly BaseAddonRoute[];
}

/** A shell registered with `createApp`: the chrome component + auth policy. */
export interface ShellConfig {
  /** Chrome wrapping route bodies (e.g. `ConsoleShell`). */
  chrome: ComponentType<{ children: ReactNode }>;
  /** Gate routes behind sign-in. Defaults to `true` for every shell but `public`. */
  requireAuth?: boolean;
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
  mount(target: string | Element): Root;
}

/**
 * `createApp` — the single composition root. It merges the addon manifests into
 * one runtime (routes · menus · widgets · i18n · slots), owns the provider stack
 * (GraphQL clients · runtime · live invalidation · auth), builds the router, and
 * wraps every route in its shell's chrome. The host writes one
 * `createApp({...}).mount(...)`.
 */
export function createApp(input: CreateAppInput): AngeeApp {
  const composed = composeAddons(input.addons as readonly AddonManifest[]);

  const pageByRoute = new Map<string, ComponentType>();
  for (const addon of input.addons) {
    for (const route of addon.routes ?? []) pageByRoute.set(route.name, route.component);
  }

  const runtime: AppRuntime = {
    widgets: { ...defaultWidgets, ...composed.widgets },
    menus: composed.menus,
    // Seed the base namespace under the merged addon bundles; an addon key wins.
    i18n: mergeI18n(enBaseBundle, composed.i18n),
    icons: composed.icons,
    chatter: composed.chatter,
    slots: mergeSlotContributions(composed.slots, input.slots ?? []),
  };

  const defaultSchema = input.defaultSchema ?? "public";
  const subscriptionSchema = input.subscriptionSchema ?? "console";
  const pathByName = new Map(composed.routes.map((route) => [route.name, route.path]));
  const home =
    resolvePath(input.home, pathByName) ??
    composed.routes.find((route) => route.shell !== "public")?.path ??
    "/";

  const rootRoute = createRootRoute({ component: RootOutlet });

  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <Redirect to={home} />,
  });

  const routeNodes = composed.routes.map((route) =>
    createRoute({
      getParentRoute: () => rootRoute,
      path: route.path,
      component: () => (
        <RouteScreen route={route} page={pageByRoute.get(route.name)} shells={input.shells} />
      ),
    }),
  );

  const router = createRouter({
    routeTree: rootRoute.addChildren([indexRoute, ...routeNodes]),
    history: typeof window === "undefined" ? createMemoryHistory() : undefined,
    defaultPreload: false,
  });

  return {
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
            <AppFrame runtime={runtime} subscriptionSchema={subscriptionSchema}>
              <RouterProvider router={router} />
            </AppFrame>
          </GraphQLClientProvider>
        </StrictMode>,
      );
      return root;
    },
  };
}

/**
 * The provider frame inside the client pool: resolve the current actor, open the
 * change subscriptions on the subscription schema's client, and expose the
 * runtime and auth state to every route.
 */
function AppFrame({
  runtime,
  subscriptionSchema,
  children,
}: {
  runtime: AppRuntime;
  subscriptionSchema: string;
  children: ReactNode;
}): ReactNode {
  const { auth } = useRuntimeAuthState();
  const clients = useSchemaClients();
  return (
    <AppRuntimeProvider runtime={runtime}>
      <ModalsHost>
        <RelayInvalidationProvider client={clients[subscriptionSchema]}>
          <AuthProvider auth={auth}>{children}</AuthProvider>
        </RelayInvalidationProvider>
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

function RouteScreen({
  route,
  page: Page,
  shells,
}: {
  route: AddonRoute;
  page: ComponentType | undefined;
  shells: Record<string, ShellConfig>;
}): ReactNode {
  const shell = shells[route.shell];
  const Chrome = shell?.chrome ?? PassthroughChrome;
  const requireAuth = shell?.requireAuth ?? route.shell !== "public";
  const body = <Chrome>{Page ? <Page /> : null}</Chrome>;
  return requireAuth ? <RequireAuth>{body}</RequireAuth> : body;
}

/** Gate a subtree behind sign-in; bounce to `/login` while or after resolving. */
function RequireAuth({ children }: { children: ReactNode }): ReactNode {
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
  if (fetching && !auth.user) return <FullPageStatus message="Loading workspace…" />;
  if (!auth.user) return <FullPageStatus message="Redirecting to sign in…" />;
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

function PassthroughChrome({ children }: { children: ReactNode }): ReactNode {
  return <>{children}</>;
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

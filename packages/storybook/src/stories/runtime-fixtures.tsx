import {
  ActiveGraphQLSchemaProvider,
  ModelMetadataProvider,
  defineAngeeSchemaMetadata,
  refineResourcesFromAngeeSchemaMetadata,
  schemaFieldMetadataFromAngeeSchemaMetadata,
  type AngeeSchemaMetadata,
} from "@angee/metadata";
import {
  useLayoutEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
  type ComponentProps,
  type ReactNode,
} from "react";
import {
  AppRuntimeProvider,
  AppearanceProvider,
  ModalsHost,
  ToastProvider,
  InAppLinkProvider,
  routerNavigator,
  baseIcons,
  ConsoleLayout,
  defaultWidgets,
  type AppRuntime,
} from "@angee/ui";
import {
  createAngeeHasuraDataProviders,
  OperationDocumentsProvider,
  Refine,
  type useQueryClient,
  tanStackRouterProvider,
  type AngeeHasuraSchemaConfig,
} from "@angee/refine";
import type { IResourceItem } from "@refinedev/core";
import { composeAddons } from "@angee/app";
import { parseFlatSearch, stringifyFlatSearch } from "@angee/app/create-app";
import { NuqsTestingAdapter } from "nuqs/adapters/testing";
import { NuqsAdapter } from "nuqs/adapters/tanstack-router";
import { previewThemes, previewThemeId } from "../theme-global";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";

export { testDataResource, testQueryAxis, testQueryField, testResourceQuery } from "@angee/metadata/testing";

/**
 * The one Storybook environment, shared by the global preview decorator and
 * direct routed fixtures. Native story parameters configure schema transport,
 * resources and runtime; direct fixture stories pass toolbar appearance from
 * their render context. Toasts and modal portals inherit this same environment.
 */

type StorySchemaConfig = AngeeHasuraSchemaConfig & {
  metadata?: AngeeSchemaMetadata;
};

/** The framework's containers as `createApp` composes them: the view switcher's kinds, the chatter aside, the shell's. */
const FRAMEWORK_CONTAINERS = composeAddons([], { canonicalModelLabel: (model) => model }).containers;

/** Keep route components stable while args and toolbar globals change. */
export function useStorySnapshot<T>(snapshot: T) {
  const latest = useRef(snapshot);
  const store = useMemo(() => {
    const listeners = new Set<() => void>();
    return {
      subscribe(listener: () => void) {
        listeners.add(listener);
        return () => { listeners.delete(listener); };
      },
      getSnapshot: () => latest.current,
      publish(next: T) {
        latest.current = next;
        listeners.forEach((listener) => listener());
      },
    };
  }, []);
  useLayoutEffect(() => {
    if (latest.current !== snapshot) store.publish(snapshot);
  }, [snapshot, store]);
  return store;
}

/** Toolbar globals are translated by native Storybook decorators/renderers. */
export function storyAppearance(globals: Record<string, unknown>): NonNullable<RuntimeFixtureProps["appearance"]> {
  return {
    themeId: previewThemeId(globals.themeId),
    colorScheme: globals.colorScheme === "dark" ? "dark" : globals.colorScheme === "light" ? "light" : "system",
  };
}

/** A JSON `Response` for a story fetch responder. */
export function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data), {
    headers: { "content-type": "application/json" },
  });
}

/**
 * Build the `public` schema config for a story from its model fetch responder,
 * answering the CSRF probe uniformly so the responder only describes its model
 * queries/mutations.
 */
export function storySchema(
  fetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
): Record<string, StorySchemaConfig> {
  return {
    public: {
      url: "/graphql/public/",
      fetch: (input, init) =>
        String(input).includes("/auth/csrf/")
          ? Promise.resolve(jsonResponse({ token: "storybook" }))
          : fetch(input, init),
    },
  };
}

export interface RuntimeFixtureProps {
  activeSchema?: string;
  schemas: Record<string, StorySchemaConfig>;
  appearance?: ComponentProps<typeof AppearanceProvider>["host"];
  children: ReactNode;
  runtime?: Partial<AppRuntime>;
  syncWithLocation?: boolean;
  resources?: IResourceItem[];
  routed?: boolean;
  operationDocuments?: ComponentProps<typeof OperationDocumentsProvider>["documents"];
  liveProvider?: ComponentProps<typeof Refine>["liveProvider"];
  queryClient?: ReturnType<typeof useQueryClient>;
}

/**
 * Wrap a data-bound view in the modal host, refine data providers over `schemas`,
 * active schema metadata, and the composed runtime — the stack every fetching
 * view needs to render in isolation.
 */
export function RuntimeFixture({
  activeSchema = "public",
  schemas,
  children,
  appearance,
  runtime = {},
  syncWithLocation = false,
  resources,
  routed = false,
  operationDocuments = {},
  liveProvider,
  queryClient,
}: RuntimeFixtureProps): ReactNode {
  const QueryStateAdapter = routed ? NuqsAdapter : NuqsTestingAdapter;
  const normalized = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(schemas).map(([name, schema]) => {
          const metadata = schema.metadata
            ? defineAngeeSchemaMetadata(schema.metadata)
            : undefined;
          return [
            name,
            {
              ...schema,
              ...(metadata ? { metadata } : {}),
            },
          ];
        }),
      ) as Record<string, StorySchemaConfig>,
    [schemas],
  );
  const dataProvider = useMemo(
    () => createAngeeHasuraDataProviders(normalized, activeSchema),
    [activeSchema, normalized],
  );
  const fieldMetadata = useMemo(
    () =>
      schemaFieldMetadataFromAngeeSchemaMetadata(normalized[activeSchema]?.metadata),
    [activeSchema, normalized],
  );
  return (
    <AppRuntimeProvider runtime={{ icons: baseIcons, widgets: defaultWidgets, containers: FRAMEWORK_CONTAINERS, themes: previewThemes, ...runtime }}>
      <AppearanceProvider host={appearance}>
        <Refine
          dataProvider={dataProvider}
          liveProvider={liveProvider}
          resources={resources ?? [...refineResourcesFromAngeeSchemaMetadata(normalized[activeSchema]?.metadata)]}
          routerProvider={routed ? tanStackRouterProvider : undefined}
          options={{ syncWithLocation, ...(queryClient ? { reactQuery: { clientConfig: queryClient } } : {}) }}
        >
          <OperationDocumentsProvider documents={operationDocuments}>
            <ActiveGraphQLSchemaProvider schema={activeSchema}>
              <ModelMetadataProvider metadata={fieldMetadata}>
                <QueryStateAdapter>
                  <ToastProvider><ModalsHost>{children}</ModalsHost></ToastProvider>
                </QueryStateAdapter>
              </ModelMetadataProvider>
            </ActiveGraphQLSchemaProvider>
          </OperationDocumentsProvider>
        </Refine>
      </AppearanceProvider>
    </AppRuntimeProvider>
  );
}

/** Native routed resource-page host for full interaction stories. */
export function RoutedRuntimeFixture({
  activeSchema = "public",
  schemas,
  children,
  appearance,
  collectionPath,
  initialEntry = collectionPath,
  recordParam = "id",
  runtime = {},
  resourceLabel = "Records",
  resourceName,
  operationDocuments,
  liveProvider,
  queryClient,
}: {
  activeSchema?: string;
  schemas: Record<string, StorySchemaConfig>;
  children: ReactNode;
  appearance?: RuntimeFixtureProps["appearance"];
  collectionPath: string;
  initialEntry?: string;
  recordParam?: string;
  runtime?: Partial<AppRuntime>;
  resourceLabel?: string;
  resourceName?: string;
  operationDocuments?: ComponentProps<typeof OperationDocumentsProvider>["documents"];
  liveProvider?: ComponentProps<typeof Refine>["liveProvider"];
  queryClient?: ReturnType<typeof useQueryClient>;
}): ReactNode {
  const store = useStorySnapshot({ activeSchema, schemas, children, appearance, runtime, resourceLabel, resourceName, operationDocuments, liveProvider, queryClient });
  const router = useMemo(() => {
    function StoryRoot() {
      const { resourceLabel, resourceName, ...fixture } = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
      const resources = resourceName ? [{
        name: resourceName,
        list: collectionPath,
        show: `${collectionPath}/:${recordParam}`,
        meta: { label: resourceLabel },
      }] : undefined;
      return <InAppLinkProvider navigate={navigate}>
        <RuntimeFixture {...fixture} resources={resources} routed syncWithLocation>
          <Outlet />
        </RuntimeFixture>
      </InAppLinkProvider>;
    }
    function StoryContent() {
      const { children } = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
      return <ConsoleLayout>{children}</ConsoleLayout>;
    }
    const root = createRootRoute({ component: StoryRoot });
    const collection = createRoute({
      getParentRoute: () => root,
      path: collectionPath,
      component: StoryContent,
    });
    const record = createRoute({
      getParentRoute: () => collection,
      path: `$${recordParam}`,
    });
    const router = createRouter({
      routeTree: root.addChildren([collection.addChildren([record])]),
      history: createMemoryHistory({ initialEntries: [initialEntry] }),
      parseSearch: parseFlatSearch,
      stringifySearch: stringifyFlatSearch,
    });
    const navigate = routerNavigator(router);
    return router;
  }, [collectionPath, initialEntry, recordParam, store]);
  return <RouterProvider router={router} />;
}

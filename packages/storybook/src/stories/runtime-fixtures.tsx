import { ModelMetadataProvider, defineAngeeSchemaMetadata, schemaFieldMetadataFromAngeeSchemaMetadata, type AngeeSchemaMetadata, } from "@angee/metadata";
import {
  useMemo, type ComponentProps, type ReactNode } from "react";
import {
  AppRuntimeProvider,
  InAppLinkProvider,
  baseIcons,
  ConsoleLayout,
  defaultWidgets,
  type AppRuntime,
} from "@angee/ui";
import { ActiveGraphQLSchemaProvider, } from "@angee/metadata";
import {
  createAngeeHasuraDataProviders,
  OperationDocumentsProvider,
  Refine,
  tanStackRouterProvider,
  type AngeeHasuraSchemaConfig,
} from "@angee/refine";
import type { IResourceItem } from "@refinedev/core";
import { composeAddons } from "@angee/app";
import { ModalsHost, ToastProvider } from "@angee/ui";
export { testDataResource, testQueryAxis, testQueryField, testResourceQuery } from "@angee/metadata/testing";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";

/**
 * Shared story fixtures for data-bound views (`ListView`/`FormView`). A view that
 * fetches needs the same provider stack — modal host, refine data provider over a
 * story fetch responder, generated resource metadata, and the composed runtime
 * (base icons + widgets) — so it lives here once instead of being re-hand-rolled
 * per story.
 */

type StorySchemaConfig = AngeeHasuraSchemaConfig & {
  metadata?: AngeeSchemaMetadata;
};

/** The framework's containers as `createApp` composes them: the view switcher's kinds, the chatter aside, the shell's. */
const FRAMEWORK_CONTAINERS = composeAddons([], { canonicalModelLabel: (model) => model }).containers;

export function RuntimeRegistryFixture({
  children,
  runtime = {},
}: {
  children: ReactNode;
  runtime?: Partial<AppRuntime>;
}): ReactNode {
  return (
    <AppRuntimeProvider
      runtime={{ icons: baseIcons, widgets: defaultWidgets, containers: FRAMEWORK_CONTAINERS, ...runtime }}
    >
      {children}
    </AppRuntimeProvider>
  );
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

/**
 * Wrap a data-bound view in the modal host, refine data providers over `schemas`,
 * active schema metadata, and the composed runtime — the stack every fetching
 * view needs to render in isolation.
 */
export function RuntimeFixture({
  activeSchema = "public",
  schemas,
  children,
  runtime = {},
  syncWithLocation = false,
  resources,
  routed = false,
  operationDocuments = {},
}: {
  activeSchema?: string;
  schemas: Record<string, StorySchemaConfig>;
  children: ReactNode;
  runtime?: Partial<AppRuntime>;
  syncWithLocation?: boolean;
  resources?: IResourceItem[];
  routed?: boolean;
  operationDocuments?: ComponentProps<typeof OperationDocumentsProvider>["documents"];
}): ReactNode {
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
    () => createAngeeHasuraDataProviders(normalized, "public"),
    [normalized],
  );
  const fieldMetadata = useMemo(
    () =>
      schemaFieldMetadataFromAngeeSchemaMetadata(normalized[activeSchema]?.metadata),
    [activeSchema, normalized],
  );
  return (
    <ToastProvider><ModalsHost>
      <Refine
        dataProvider={dataProvider}
        resources={resources}
        routerProvider={routed ? tanStackRouterProvider : undefined}
        options={{ syncWithLocation }}
      >
        <OperationDocumentsProvider documents={operationDocuments}><ActiveGraphQLSchemaProvider schema={activeSchema}>
          <ModelMetadataProvider metadata={fieldMetadata}>
            <RuntimeRegistryFixture runtime={runtime}>
              {children}
            </RuntimeRegistryFixture>
          </ModelMetadataProvider>
        </ActiveGraphQLSchemaProvider></OperationDocumentsProvider>
      </Refine>
    </ModalsHost></ToastProvider>
  );
}

/** Native routed resource-page host for full interaction stories. */
export function RoutedRuntimeFixture({
  activeSchema = "public",
  schemas,
  children,
  collectionPath,
  initialEntry = collectionPath,
  recordParam = "id",
  runtime = {},
  resourceLabel = "Records",
  resourceName,
  operationDocuments,
}: {
  activeSchema?: string;
  schemas: Record<string, StorySchemaConfig>;
  children: ReactNode;
  collectionPath: string;
  initialEntry?: string;
  recordParam?: string;
  runtime?: Partial<AppRuntime>;
  resourceLabel?: string;
  resourceName?: string;
  operationDocuments?: ComponentProps<typeof OperationDocumentsProvider>["documents"];
}): ReactNode {
  const router = useMemo(() => {
    const resources = resourceName ? [{
      name: resourceName,
      list: collectionPath,
      show: `${collectionPath}/:${recordParam}`,
      meta: { label: resourceLabel },
    }] : undefined;
    const root = createRootRoute({ component: () => <InAppLinkProvider navigate={(href, options) => { void router.navigate({ href, ...options }); }}><RuntimeFixture
      activeSchema={activeSchema}
      schemas={schemas}
      runtime={runtime}
      resources={resources}
      routed
      syncWithLocation
      operationDocuments={operationDocuments}
    ><Outlet /></RuntimeFixture></InAppLinkProvider> });
    const collection = createRoute({
      getParentRoute: () => root,
      path: collectionPath,
      component: () => <ConsoleLayout>{children}</ConsoleLayout>,
    });
    const record = createRoute({
      getParentRoute: () => collection,
      path: `$${recordParam}`,
    });
    const router = createRouter({
      routeTree: root.addChildren([collection.addChildren([record])]),
      history: createMemoryHistory({ initialEntries: [initialEntry] }),
      parseSearch: (value) => Object.fromEntries(new URLSearchParams(value)),
      stringifySearch: (value) => {
        const query = new URLSearchParams(Object.entries(value).flatMap(([key, item]) => (
          item == null ? [] : [[key, String(item)]]
        ))).toString();
        return query ? `?${query}` : "";
      },
    });
    return router;
  }, [activeSchema, children, collectionPath, initialEntry, operationDocuments, recordParam, resourceLabel, resourceName, runtime, schemas]);
  return <RouterProvider router={router} />;
}

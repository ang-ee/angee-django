import {
  ModelMetadataProvider,
  defineAngeeSchemaMetadata,
  refineResourcesFromAngeeSchemaMetadata,
  schemaFieldMetadataFromAngeeSchemaMetadata,
  type AngeeSchemaMetadata,
  type DataResourceFieldMetadata,
  type DataResourceMetadata,
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
  ActiveDataProviderNameProvider,
  createAngeeHasuraDataProviders,
  OperationDocumentsProvider,
  Refine,
  type useQueryClient,
  tanStackRouterProvider,
  type AngeeHasuraSchemaConfig,
} from "@angee/refine";
import type { IResourceItem } from "@refinedev/core";
import { graphqlWireFetch, type GraphQLWireOperation, type GraphQLWireRequest } from "@angee/refine/testing/wire";
import { composeAddons } from "@angee/app";
import { createApp, parseFlatSearch, retryAppQuery, stringifyFlatSearch, type CreateAppInput } from "@angee/app/create-app";
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

/**
 * A story's reads fail as the app's do: a refused request (a public GraphQL answer) is not
 * retried, where the data library's own default retries every failure three times and a story
 * about a refusal showed its error seconds late. Used when a story brings no client of its own.
 * Only the retry rule is the app's: the client's other defaults (how long a read stays fresh and
 * how long it is kept) are still the data library's, not the app's.
 */
const FIXTURE_QUERY_CLIENT_CONFIG = { defaultOptions: { queries: { retry: retryAppQuery } } };

export { testDataResource, testQueryAxis, testQueryField, testResourceQuery } from "@angee/metadata/testing";

/** A story resource's field: a readable `String` scalar a person may create and update; override what the story needs. */
export function storyField(name: string, overrides: Partial<DataResourceFieldMetadata> = {}): DataResourceFieldMetadata {
  return {
    name, kind: "scalar", scalar: "String", readable: true, aggregatable: false,
    creatable: true, updatable: true, requiredOnCreate: false, ...overrides,
  };
}
export { GraphQLError, type GraphQLWireOperation, type GraphQLWireRequest } from "@angee/refine/testing/wire";
export { resourceSnapshotOperations } from "../resource-snapshots";

/** Full product pages use the production composition root and native memory history.
 * The caller owns stable fixture input, including every schema's isolated transport.
 * The workshop's toolbar is the host's appearance, so the composed addons' run-time
 * `hostAppearance` source is left out, and its reads need no handler, unless the story
 * is about that source (`hostAppearanceSource`).
 * Set angeeOwnRuntime so the global decorator does not mount a second provider stack.
 */
export function ComposedRuntimeFixture({ input, initialEntry, hostAppearanceSource = false }: {
  input: CreateAppInput;
  initialEntry: string;
  /** Install the composed addons' run-time host appearance source; the story's transports then answer its reads. */
  hostAppearanceSource?: boolean;
}) {
  const app = useMemo(() => {
    for (const [name, schema] of Object.entries(input.schemas)) {
      if (!schema.fetch || schema.live !== false) {
        throw new Error(`Story schema ${name} needs a fixture fetch and live subscriptions disabled.`);
      }
    }
    const addons = hostAppearanceSource ? input.addons : input.addons.map(({ hostAppearance: _source, ...addon }) => addon);
    return createApp({ ...input, addons, history: createMemoryHistory({ initialEntries: [initialEntry] }) });
  }, [input, initialEntry, hostAppearanceSource]);
  return <RouterProvider router={app.router} />;
}

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

export interface StorySchemaOptions {
  /** The schema's name, served at `/graphql/<schema>/`; `public` unless given. */
  schema?: string;
  /**
   * The data resources the schema's metadata names, as a composed host's `angee.resources`
   * does; each declares this schema as its `schemaName` (`testDataResource` defaults to
   * `console`). A resource surface finds its model only here: without it, it finds no model,
   * warns and loads no record.
   */
  resources?: readonly DataResourceMetadata[];
  /** Receives each request the transport parses. */
  onRequest?: (request: GraphQLWireRequest) => void;
}

/**
 * The schema config of a story or native test, keyed by its name, whose transport
 * is refine's GraphQL wire fake: each root field is answered by the handler named
 * for it, and an unhandled field or a missing required variable fails loudly.
 * Its metadata names `resources`; a resource of another schema is refused.
 */
export function storySchema(
  operations: Readonly<Record<string, GraphQLWireOperation>>,
  { schema = "public", resources, onRequest }: StorySchemaOptions = {},
): Record<string, StorySchemaConfig> {
  const foreign = resources?.find((resource) => resource.schemaName !== schema);
  if (foreign) throw new Error(`Story schema ${schema} cannot name ${foreign.modelLabel}, a resource of schema ${foreign.schemaName}.`);
  return { [schema]: {
    url: `/graphql/${schema}/`,
    fetch: graphqlWireFetch(operations, onRequest),
    ...(resources ? { metadata: { angee: { resources } } } : {}),
  } };
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
  /** Receives the data layer's notices, for a test that records what the data layer tells; none open without it. */
  notificationProvider?: ComponentProps<typeof Refine>["notificationProvider"];
  queryClient?: ReturnType<typeof useQueryClient>;
}

/**
 * Wrap a data-bound view in the modal host, refine data providers over `schemas`,
 * the active schema's name and metadata, and the composed runtime — the stack every
 * fetching view needs to render in isolation. The data layer tells its notices only
 * to a given `notificationProvider`; the app's `useConnectionNotifications` is not
 * mounted, so through the fixture a test records which notices the data layer opens
 * for a surface's calls, not what `useConnectionNotifications` holds back or tells once.
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
  notificationProvider,
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
          notificationProvider={notificationProvider}
          resources={resources ?? [...refineResourcesFromAngeeSchemaMetadata(normalized[activeSchema]?.metadata)]}
          routerProvider={routed ? tanStackRouterProvider : undefined}
          options={{ syncWithLocation, disableTelemetry: true, reactQuery: { clientConfig: queryClient ?? FIXTURE_QUERY_CLIENT_CONFIG } }}
        >
          <OperationDocumentsProvider documents={operationDocuments}>
            {/* As the app's layout route does: hooks outside a resource surface resolve the active schema. */}
            <ActiveDataProviderNameProvider name={activeSchema}>
              <ModelMetadataProvider metadata={fieldMetadata}>
                <QueryStateAdapter>
                  <ToastProvider><ModalsHost>{children}</ModalsHost></ToastProvider>
                </QueryStateAdapter>
              </ModelMetadataProvider>
            </ActiveDataProviderNameProvider>
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
  notificationProvider,
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
  notificationProvider?: RuntimeFixtureProps["notificationProvider"];
  queryClient?: ReturnType<typeof useQueryClient>;
}): ReactNode {
  const store = useStorySnapshot({ activeSchema, schemas, children, appearance, runtime, resourceLabel, resourceName, operationDocuments, liveProvider, notificationProvider, queryClient });
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

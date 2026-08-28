import hasuraDataProvider, {
  GraphQLClient,
  graphqlWS,
  type HasuraDataProviderOptions,
} from "@refinedev/hasura";
import type {
  DataProvider,
  DataProviders,
  LiveEvent,
  LiveProvider,
} from "@refinedev/core";
import type { QueryClient } from "@tanstack/react-query";
import {
  graphQLWebSocketUrl,
  sessionAuth,
  type AuthFetch,
} from "./transport-auth";
import {
  operationName,
  recordValue,
  stringValue,
} from "./dialect/wire";
import { authoredQueryReadsAnyModel } from "./query-invalidation";

type FetchFn = typeof globalThis.fetch;
type GraphQLWsClient = ReturnType<typeof graphqlWS.createClient>;
const noopSubscription = () => undefined;

export const ANGEE_HASURA_PROVIDER_OPTIONS = {
  idType: "String",
  namingConvention: "hasura-default",
} satisfies HasuraDataProviderOptions;

export interface AngeeHasuraClientOptions {
  url: string;
  headers?: HeadersInit;
  auth?: AuthFetch;
  csrfEndpoint?: string;
  fetch?: FetchFn;
}

export interface AngeeHasuraDataProviderOptions
  extends AngeeHasuraClientOptions {
  providerOptions?: HasuraDataProviderOptions;
}

export type AngeeHasuraWebSocketOptions =
  Omit<Parameters<typeof graphqlWS.createClient>[0], "url"> & {
    url?: string;
  };

export interface AngeeHasuraLiveProviderOptions {
  url: string;
  wsEndpoint?: string;
  origin?: string;
  clientOptions?: AngeeHasuraWebSocketOptions;
  resources?: readonly AngeeLiveResource[];
  queryClient?: AuthoredQueryInvalidationClient;
}

export interface AngeeHasuraSchemaConfig
  extends AngeeHasuraDataProviderOptions {
  sdl?: string;
  metadata?: unknown;
  live?: AngeeHasuraLiveProviderOptions | boolean;
}

export interface AngeeLiveResource {
  schemaName: string;
  modelLabel: string;
  roots: {
    list?: string | null;
    changes?: string | null;
  };
}

export function createAngeeGraphQLClient(
  options: AngeeHasuraClientOptions,
): GraphQLClient {
  const baseFetch = options.fetch ?? globalThis.fetch;
  const auth = options.auth ?? sessionAuth({
    endpoint: options.csrfEndpoint,
    fetch: baseFetch,
  });
  return new GraphQLClient(options.url, {
    fetch: auth(baseFetch),
    headers: options.headers,
  });
}

export function createAngeeHasuraDataProvider(
  options: AngeeHasuraDataProviderOptions,
): Required<DataProvider> {
  return hasuraDataProvider(
    createAngeeGraphQLClient(options),
    hasuraOptions(options.providerOptions),
  );
}

export function createAngeeHasuraDataProviders(
  schemas: Readonly<Record<string, AngeeHasuraSchemaConfig>>,
  defaultSchema?: string,
): DataProviders {
  const providers = Object.fromEntries(
    Object.entries(schemas).map(([name, options]) => [
      name,
      createAngeeHasuraDataProvider(options),
    ]),
  ) as Record<string, Required<DataProvider>>;
  const defaultProvider =
    providers[defaultSchema ?? ""]
    ?? providers[Object.keys(providers).sort()[0] ?? ""];
  if (!defaultProvider) {
    throw new Error("createAngeeHasuraDataProviders requires at least one schema.");
  }
  return {
    ...providers,
    default: defaultProvider,
  };
}

export function createAngeeHasuraLiveProvider(
  options: AngeeHasuraLiveProviderOptions,
): LiveProvider {
  const wsClient = graphqlWS.createClient({
    ...options.clientOptions,
    url: options.clientOptions?.url
      ?? resolveGraphQLWebSocketEndpoint(
        options.wsEndpoint ?? options.url,
        options.origin,
      ),
  });
  return createAngeeChangeLiveProvider(wsClient, options.resources ?? [], {
    queryClient: options.queryClient,
  });
}

type ChangeConsumer = (data: unknown) => void;

interface ChangeSubscription {
  dispose: () => void;
  consumers: Set<ChangeConsumer>;
}

type AuthoredQueryInvalidationClient = Pick<QueryClient, "invalidateQueries">;

export function createAngeeChangeLiveProvider(
  client: GraphQLWsClient,
  resources: readonly AngeeLiveResource[],
  options: { queryClient?: AuthoredQueryInvalidationClient } = {},
): LiveProvider {
  const resourcesByList = resourcesByListRoot(resources);
  const resourcesByModel = resourcesByModelLabel(resources);
  // graphql-ws does not dedup identical documents, so fan one upstream
  // subscription per changes root out to every mounted consumer — resource hooks
  // (useList/useOne, keyed by list root) and authored queries (keyed by model
  // label) alike — and tear the socket subscription down only when the last
  // consumer leaves.
  const subscriptions = new Map<string, ChangeSubscription>();

  // Attach one fan-in consumer to the shared upstream for `changesRoot`, opening
  // the socket subscription on the first consumer; the returned disposer removes
  // that consumer and closes the socket once the last one leaves.
  function joinChangesRoot(
    changesRoot: string,
    resource: AngeeLiveResource,
    channel: string,
    callback: (event: LiveEvent) => void,
  ): () => void {
    const consumer: ChangeConsumer = (data) => {
      const event = changeEventFromResult(data, changesRoot, channel, resource);
      if (event) {
        invalidateAuthoredQueriesForEvent(options.queryClient, event);
        callback(event);
      }
    };
    const entry = subscriptions.get(changesRoot) ?? {
      dispose: noopSubscription,
      consumers: new Set<ChangeConsumer>(),
    };
    entry.consumers.add(consumer);
    if (!subscriptions.has(changesRoot)) {
      subscriptions.set(changesRoot, entry);
      entry.dispose = client.subscribe(
        { query: changeSubscriptionDocument(changesRoot) },
        {
          next: (result) => entry.consumers.forEach((c) => c(result.data)),
          error: (error) => {
            console.error(
              "Angee live subscription failed; the next subscriber will reconnect.",
              { changesRoot, model: resource.modelLabel },
              error,
            );
            if (subscriptions.get(changesRoot) === entry) {
              entry.dispose();
              subscriptions.delete(changesRoot);
            }
          },
          complete: () => undefined,
        },
      );
    }
    return () => {
      entry.consumers.delete(consumer);
      if (entry.consumers.size === 0 && subscriptions.get(changesRoot) === entry) {
        entry.dispose();
        subscriptions.delete(changesRoot);
      }
    };
  }

  return {
    subscribe({ channel, callback, params }) {
      const targets = changeTargetsFromSubscribeParams(
        params,
        resourcesByList,
        resourcesByModel,
      );
      if (targets.length === 0) return noopSubscription;
      const disposers = targets.map(({ resource, changesRoot }) =>
        joinChangesRoot(changesRoot, resource, channel, callback),
      );
      return () => disposers.forEach((dispose) => dispose());
    },
    unsubscribe(subscription) {
      if (typeof subscription === "function") subscription();
    },
  };
}

export function resolveGraphQLWebSocketEndpoint(
  endpoint: string,
  origin?: string,
): string {
  const base =
    origin ?? (typeof location !== "undefined" ? location.origin : undefined);
  const url = new URL(endpoint, base);
  if (url.protocol === "ws:" || url.protocol === "wss:") {
    return url.toString();
  }
  return graphQLWebSocketUrl(endpoint, origin);
}

function invalidateAuthoredQueriesForEvent(
  queryClient: AuthoredQueryInvalidationClient | undefined,
  event: LiveEvent,
): void {
  const model = stringValue(recordValue(event.payload)?.model);
  if (!queryClient || !model) return;
  void queryClient.invalidateQueries({
    predicate: (query) => authoredQueryReadsAnyModel(query.meta, [model]),
    type: "all",
    refetchType: "active",
  });
}

function hasuraOptions(
  options: HasuraDataProviderOptions | undefined,
): HasuraDataProviderOptions {
  return {
    ...ANGEE_HASURA_PROVIDER_OPTIONS,
    ...options,
  };
}

type LiveSubscribeParams = Parameters<LiveProvider["subscribe"]>[0]["params"];

interface ChangeTarget {
  resource: AngeeLiveResource;
  changesRoot: string;
}

function resourcesByListRoot(
  resources: readonly AngeeLiveResource[],
): ReadonlyMap<string, AngeeLiveResource> {
  return new Map(
    resources.flatMap((resource) =>
      resource.roots.list ? [[resource.roots.list, resource] as const] : [],
    ),
  );
}

function resourcesByModelLabel(
  resources: readonly AngeeLiveResource[],
): ReadonlyMap<string, AngeeLiveResource> {
  return new Map(
    resources.map((resource) => [resource.modelLabel, resource] as const),
  );
}

// Resolve a subscribe request to the changes roots it wants: resource hooks name
// their refine resource (a list root); authored queries declare the model labels
// they read. Both resolve through the registry the data providers were built
// from, so the changes-root mapping lives here, never hand-coded in the hook.
function changeTargetsFromSubscribeParams(
  params: LiveSubscribeParams,
  byListRoot: ReadonlyMap<string, AngeeLiveResource>,
  byModelLabel: ReadonlyMap<string, AngeeLiveResource>,
): ChangeTarget[] {
  const listRoot = typeof params?.resource === "string" ? params.resource : undefined;
  const resources = listRoot
    ? [byListRoot.get(listRoot)]
    : modelLabelsFromSubscribeParams(params).map((label) => byModelLabel.get(label));
  return resources.flatMap((resource) => {
    const changesRoot = resource?.roots.changes;
    return resource && changesRoot ? [{ resource, changesRoot }] : [];
  });
}

function modelLabelsFromSubscribeParams(params: LiveSubscribeParams): string[] {
  const models = params?.models;
  return Array.isArray(models)
    ? models.filter((model): model is string => typeof model === "string")
    : [];
}

function changeSubscriptionDocument(changesRoot: string): string {
  const root = operationName(changesRoot);
  // The schema's ChangeEvent fields are snake_case (Hasura naming); alias the
  // multi-word ones to the camelCase keys `changeEventFromResult` reads.
  return (
    `subscription angee_${root} { ` +
    `${root} { model id action ` +
    `changedFields: changed_fields changedValues: changed_values } }`
  );
}

function changeEventFromResult(
  data: unknown,
  changesRoot: string,
  channel: string,
  resource: AngeeLiveResource,
): LiveEvent | null {
  const event = recordValue(recordValue(data)?.[changesRoot]);
  const id = stringValue(event?.id);
  const action = stringValue(event?.action) ?? "*";
  return {
    channel,
    type: liveEventType(action),
    payload: {
      ...(id ? { id, ids: [id] } : {}),
      model: stringValue(event?.model) ?? resource.modelLabel,
      action,
      changedFields: Array.isArray(event?.changedFields) ? event.changedFields : [],
      changedValues: recordValue(event?.changedValues) ?? {},
    },
    date: new Date(),
    meta: {
      dataProviderName: resource.schemaName,
    },
  };
}

function liveEventType(action: string): LiveEvent["type"] {
  if (action === "create") return "created";
  if (action === "update") return "updated";
  if (action === "delete") return "deleted";
  return action;
}

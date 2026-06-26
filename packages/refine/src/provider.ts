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
import {
  graphQLWebSocketUrl,
  sessionAuth,
  type AuthFetch,
} from "./transport-auth";

type FetchFn = typeof globalThis.fetch;
type GraphQLWsClient = ReturnType<typeof graphqlWS.createClient>;
const GRAPHQL_NAME = /^[_A-Za-z][_0-9A-Za-z]*$/;
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
  return createAngeeChangeLiveProvider(wsClient, options.resources ?? []);
}

export function createAngeeChangeLiveProvider(
  client: GraphQLWsClient,
  resources: readonly AngeeLiveResource[],
): LiveProvider {
  const resourcesByName = resourcesByListRoot(resources);
  return {
    subscribe({ channel, callback, params }) {
      const resourceName = typeof params?.resource === "string" ? params.resource : "";
      const resource = resourcesByName.get(resourceName);
      const changesRoot = resource?.roots.changes;
      if (!changesRoot) return noopSubscription;
      return client.subscribe(
        {
          query: changeSubscriptionDocument(changesRoot),
        },
        {
          next: (result) => {
            const event = changeEventFromResult(
              result.data,
              changesRoot,
              channel,
              resource,
            );
            if (event) callback(event);
          },
          error: () => undefined,
          complete: () => undefined,
        },
      );
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

function hasuraOptions(
  options: HasuraDataProviderOptions | undefined,
): HasuraDataProviderOptions {
  return {
    ...ANGEE_HASURA_PROVIDER_OPTIONS,
    ...options,
  };
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

function changeSubscriptionDocument(changesRoot: string): string {
  const root = assertGraphQLName(changesRoot);
  return (
    `subscription angee_${root} { ` +
    `${root} { model id action changedFields changedValues } }`
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

function assertGraphQLName(name: string): string {
  if (!GRAPHQL_NAME.test(name)) {
    throw new Error(`Invalid GraphQL name: ${name}`);
  }
  return name;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

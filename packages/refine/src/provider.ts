import hasuraDataProvider, {
  GraphQLClient,
  graphqlWS,
  type HasuraDataProviderOptions,
} from "@refinedev/hasura";
import {
  keys,
  type DataProvider,
  type DataProviders,
  type LiveEvent,
  type LiveProvider,
  type MetaQuery,
} from "@refinedev/core";
import { resourceMutationMeta, type ResourceMutationOperations, type ResourceMutationTarget } from "./operations";
import type { QueryClient, QueryKey } from "@tanstack/react-query";
import { ServerUnavailableError, type ServerConnection } from "./server-connection";
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
import {
  catchUpAuthoredQueries,
  createLiveInvalidation,
  retainedAuthoredQueryHashes,
  type LiveChange,
  type LiveDelivery,
} from "./query-invalidation";

type FetchFn = typeof globalThis.fetch;
export type GraphQLWsClient = ReturnType<typeof graphqlWS.createClient>;
const noopSubscription = () => undefined;

export const ANGEE_HASURA_PROVIDER_OPTIONS = {
  idType: "String",
  namingConvention: "hasura-default",
} satisfies HasuraDataProviderOptions;

export interface AngeeHasuraClientOptions {
  /** Resolve an asynchronously configured connection at request time. */
  url: string | (() => string);
  headers?: HeadersInit;
  auth?: AuthFetch;
  csrfEndpoint?: string;
  fetch?: FetchFn;
  /** Shared availability gate for this app's server, before session bootstrap. */
  connection?: ServerConnection;
  /**
   * Which server error messages reach the UI. `public` (the default) keeps only
   * errors whose code the console marks public; `all` keeps every message and its
   * extensions, for an admin surface whose server writes its errors for the people
   * operating it (the operator daemon).
   */
  errorMessages?: "public" | "all";
}

export interface AngeeHasuraDataProviderOptions
  extends AngeeHasuraClientOptions {
  providerOptions?: HasuraDataProviderOptions;
  /** Root-argument capabilities keyed by the provider's list resource name. */
  mutations?: Readonly<Record<string, ResourceMutationOperations>>;
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
  queryClient?: LiveQueryClient;
}

export interface AngeeHasuraSchemaConfig
  extends AngeeHasuraDataProviderOptions {
  /** Composed schemas have a deployment URL; asynchronously resolved providers register separately. */
  url: string;
  sdl?: string;
  metadata?: unknown;
  live?: AngeeHasuraLiveProviderOptions | boolean;
}

export interface AngeeLiveResource {
  schemaName: string;
  modelLabel: string;
  /** Registered Refine identifier; a resource without an alias uses its list root. */
  identifier?: string;
  roots: {
    list?: string | null;
    changes?: string | null;
  };
}

export function createAngeeGraphQLClient(
  options: AngeeHasuraClientOptions,
): GraphQLClient {
  const fetchImpl = options.fetch ?? globalThis.fetch;
  const baseFetch = options.connection?.fetch(fetchImpl) ?? fetchImpl;
  const auth = options.auth ?? sessionAuth({
    endpoint: options.csrfEndpoint,
    fetch: baseFetch,
  });
  const withAuth = auth(baseFetch);
  // Gate before CSRF too: an unavailable server receives no session bootstrap.
  const authenticatedFetch = options.connection?.fetch(withAuth) ?? withAuth;
  const requestFetch: FetchFn = (input, init) =>
    authenticatedFetch(typeof options.url === "function" ? options.url() : input, init);
  return new GraphQLClient(typeof options.url === "string" ? options.url : "", {
    fetch: requestFetch,
    headers: options.headers,
    responseMiddleware(response) {
      if (response instanceof Error) throw boundedGraphQLTransportError(response, options.errorMessages);
    },
  });
}

export function createAngeeHasuraDataProvider(
  options: AngeeHasuraDataProviderOptions,
): Required<DataProvider> {
  const provider = hasuraDataProvider(
    createAngeeGraphQLClient(options),
    hasuraOptions(options.providerOptions),
  );
  return {
    ...provider,
    getList: (params) => provider.getList({ ...params, meta: readSelection(params.meta) }),
    getOne: (params) => provider.getOne({ ...params, meta: readSelection(params.meta) }),
    getMany: (params) => provider.getMany({ ...params, meta: readSelection(params.meta) }),
    create: (params) => provider.create({ ...params, meta: mutationSelection(
      "create", params.resource, options.mutations?.[params.resource]?.create, params.meta,
    ) }),
    update: (params) => provider.update({ ...params, meta: mutationSelection(
      "update", params.resource, options.mutations?.[params.resource]?.update, params.meta, params.id,
    ) }),
  };
}

function mutationSelection(
  kind: "create" | "update",
  resource: string,
  target: ResourceMutationTarget | undefined,
  meta: MetaQuery | undefined,
  id?: string | number,
): MetaQuery | undefined {
  if (!target?.arguments.length || meta?.gqlMutation || meta?.gqlQuery) return meta;
  const variables = recordValue(meta?.gqlVariables) ?? {};
  return {
    ...meta,
    ...resourceMutationMeta(kind, resource, target, meta?.fields ?? ["id"], variables, id),
  };
}

/** Native inventory and picker reads need a valid identity selection when none is declared. */
function readSelection(meta: MetaQuery | undefined): MetaQuery {
  if (meta && (meta.gqlQuery || meta.gqlMutation || hasFieldSelection(meta.fields))) return meta;
  // Refine's lazy signal must stay unread until a transport supports cancellation.
  return Object.defineProperties({}, {
    ...Object.getOwnPropertyDescriptors(meta ?? {}),
    fields: { value: ["id"], enumerable: true, configurable: true, writable: true },
  });
}

function hasFieldSelection(fields: unknown): boolean {
  if (Array.isArray(fields)) return fields.length > 0;
  return fields !== null && typeof fields === "object" && Object.keys(fields).length > 0;
}

export function boundedGraphQLTransportError(
  value: unknown,
  errorMessages: AngeeHasuraClientOptions["errorMessages"] = "public",
): Error {
  if (value instanceof ServerUnavailableError) return value;
  const record = recordValue(value);
  if (!record || (!("request" in record) && !("response" in record))) {
    return new Error("Request failed.");
  }
  const response = recordValue(record.response);
  const project = errorMessages === "all" ? serverGraphQLError : publicGraphQLError;
  const errors = Array.isArray(response?.errors)
    ? response.errors.flatMap((error) => project(error) ?? [])
    : [];
  const message = errors.map((error) => error.message).join(" ") || "Request failed.";
  return Object.assign(new Error(message), {
    response: {
      status: response?.status,
      errors,
    },
    graphQLErrors: errors,
  });
}

export interface PublicGraphQLError {
  message: string;
  extensions: Record<string, unknown>;
}

/** Any server error with a message, extensions kept: for a client that shows every message. */
function serverGraphQLError(value: unknown): PublicGraphQLError | null {
  const error = recordValue(value);
  if (typeof error?.message !== "string") return null;
  return { message: error.message, extensions: recordValue(error.extensions) ?? {} };
}

export function publicGraphQLError(value: unknown): PublicGraphQLError | null {
  const error = recordValue(value);
  const extensions = recordValue(error?.extensions);
  const code = extensions?.code;
  if (typeof error?.message !== "string" || !isPublicGraphQLErrorCode(code)) return null;
  return {
    message: error.message,
    extensions: {
      code,
      ...(code === "VALIDATION"
        ? {
            validationErrors: extensions?.validationErrors,
            formErrors: extensions?.formErrors,
          }
        : code === "STALE_REVISION" && (extensions?.current_revision === null
          || typeof extensions?.current_revision === "number" && Number.isSafeInteger(extensions.current_revision) && extensions.current_revision >= 0)
          ? { current_revision: extensions.current_revision } : {}),
    },
  };
}

export function isPublicGraphQLErrorCode(code: unknown): boolean {
  return code === "VALIDATION" || code === "BAD_USER_INPUT"
    || code === "STALE_REVISION" || code === "CREATION_KEY_CONFLICT" || code === "VIEW_AS_READ_ONLY"
    || code === "UNAUTHENTICATED" || code === "PERMISSION_DENIED" || code === "FORBIDDEN";
}

/** Read and safely project the native graphql-request and urql error containers. */
export function publicGraphQLErrorsFromUnknown(value: unknown): readonly PublicGraphQLError[] {
  const record = recordValue(value);
  if (!record) return [];
  const response = recordValue(record.response);
  const errors = Array.isArray(record.graphQLErrors)
    ? record.graphQLErrors
    : Array.isArray(response?.errors) ? response.errors : [];
  return errors
    .map(publicGraphQLError)
    .filter((error): error is PublicGraphQLError => error !== null);
}

/**
 * Whether a failed query is worth retrying: a transport or server failure is,
 * a public GraphQL answer (validation, permission, conflict, sign-in) is not,
 * because a retry repeats the same answer. A shared server outage is retried
 * once by the app's recovery query, not by every failed read.
 */
export function retryableQueryError(error: unknown): boolean {
  return !(error instanceof ServerUnavailableError) && publicGraphQLErrorsFromUnknown(error).length === 0;
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
): AngeeChangeLiveProvider {
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

interface ChangeEvent extends LiveChange {
  action: string;
  changedFields: readonly string[];
  changedValues: Record<string, unknown>;
}

type ChangeConsumer = LiveDelivery<ChangeEvent> | ((change: ChangeEvent) => void);

interface ChangeSubscription {
  dispose: () => void;
  start: () => void;
  consumers: Set<ChangeConsumer>;
}

export interface AngeeChangeLiveProvider extends LiveProvider {
  /** Retain mounted consumers while closing all upstream change subscriptions. */
  setEnabled: (enabled: boolean) => void;
}

type LiveQueryClient = Pick<QueryClient, "invalidateQueries" | "getQueryCache">;

export function createAngeeChangeLiveProvider(
  client: Pick<GraphQLWsClient, "subscribe" | "on">,
  resources: readonly AngeeLiveResource[],
  options: { queryClient?: LiveQueryClient } = {},
): AngeeChangeLiveProvider {
  const resourcesByList = resourcesByListRoot(resources);
  const resourcesByModel = resourcesByModelLabel(resources);
  // graphql-ws does not dedup identical documents, so fan one upstream
  // subscription per changes root out to every mounted consumer — resource hooks
  // (useList/useOne, keyed by list root) and authored queries (keyed by model
  // label) alike — and tear the socket subscription down only when the last
  // consumer leaves.
  const subscriptions = new Map<string, ChangeSubscription>();
  let enabled = true;
  let hasConnected = false;
  let catchUpAfterEnable = false;
  let startedSubscriptions = 0;
  let retainedBeforeIdleReopen: Set<string> | undefined;
  let stopConnectionListener: () => void = noopSubscription;
  const liveInvalidation = createLiveInvalidation<ChangeEvent>(options.queryClient);

  function stopUnusedConnectionListener(): void {
    if (subscriptions.size === 0) {
      liveInvalidation.clear();
      stopConnectionListener();
      stopConnectionListener = noopSubscription;
      retainedBeforeIdleReopen = undefined;
      catchUpAfterEnable = false;
    }
  }

  // Attach one fan-in consumer to the shared upstream for `changesRoot`, opening
  // the socket subscription on the first consumer; the returned disposer removes
  // that consumer and closes the socket once the last one leaves.
  function joinChangesRoot(
    changesRoot: string,
    resource: AngeeLiveResource,
    channel: string,
    callback: (event: LiveEvent) => void,
    queryKey?: QueryKey,
  ): () => void {
    const entry = subscriptions.get(changesRoot) ?? {
      dispose: noopSubscription,
      start: noopSubscription,
      consumers: new Set<ChangeConsumer>(),
    };
    const deliver = (changes: readonly ChangeEvent[]) => {
      if (enabled && entry.consumers.has(consumer)) callback(changeEvent(changes, channel, resource));
    };
    // Direct subscriptions consume individual event data (e.g. preferences).
    // Only resource-hook callbacks initiate Refine's automatic invalidation.
    const consumer: ChangeConsumer = queryKey
      ? { queryKey, callback: deliver }
      : (change) => deliver([change]);
    entry.consumers.add(consumer);
    if (!subscriptions.has(changesRoot)) {
      if (subscriptions.size === 0) {
        // Changes have no replay cursor. Catch-up is owed for retained reads
        // when subscriptions resume after a gap, not for a page's first socket
        // connection while its initial reads are still loading. An initial
        // connection also closes the gap between HTTP and subscribing, but
        // leaving first loads in flight accepts a possible stale first response.
        stopConnectionListener = client.on("connected", (_socket, _payload, wasRetry) => {
          const reconnect = hasConnected || wasRetry || catchUpAfterEnable;
          hasConnected = true;
          catchUpAfterEnable = false;
          const retainedAtStart = retainedBeforeIdleReopen;
          retainedBeforeIdleReopen = undefined;
          if (!enabled || !reconnect) return;
          const models = resources
            .filter((resource) => subscriptions.has(resource.roots.changes ?? ""))
            .map((resource) => resource.modelLabel);
          if (options.queryClient) void catchUpAuthoredQueries(
            options.queryClient, models, wasRetry ? undefined : retainedAtStart, wasRetry,
          );
        });
      }
      subscriptions.set(changesRoot, entry);
      entry.start = () => {
        if (startedSubscriptions === 0 && hasConnected && !catchUpAfterEnable && options.queryClient) {
          retainedBeforeIdleReopen = retainedAuthoredQueryHashes(
            options.queryClient,
            resources.filter((candidate) => candidate.roots.changes).map((candidate) => candidate.modelLabel),
          );
        }
        // Ignore late deliveries from a disposed subscription, even after resume.
        let active = true;
        const dispose = client.subscribe(
          { query: changeSubscriptionDocument(changesRoot) },
          {
            next: (result) => {
              if (!active || !enabled) return;
              // One upstream result is one change, however many consumers share it.
              const change = liveChangeFromResult(result.data, changesRoot, resource);
              const deliveries = [...entry.consumers].filter(
                (consumer): consumer is LiveDelivery<ChangeEvent> => typeof consumer !== "function",
              );
              liveInvalidation.push(change, deliveries);
              entry.consumers.forEach((consumer) => {
                if (typeof consumer === "function") consumer(change);
              });
            },
            error: (error) => {
              if (!active) return;
              console.error(
                "Angee live subscription failed; the next subscriber will reconnect.",
                { changesRoot, model: resource.modelLabel },
                error,
              );
              if (subscriptions.get(changesRoot) === entry) {
                entry.dispose();
                entry.consumers.clear();
                subscriptions.delete(changesRoot);
                stopUnusedConnectionListener();
              }
            },
            complete: () => undefined,
          },
        );
        startedSubscriptions++;
        entry.dispose = () => {
          if (active) {
            active = false;
            entry.consumers.forEach((consumer) => {
              if (typeof consumer !== "function") liveInvalidation.remove(consumer);
            });
            startedSubscriptions--;
            dispose();
          }
        };
      };
      if (enabled) entry.start();
    }
    return () => {
      entry.consumers.delete(consumer);
      if (typeof consumer !== "function") liveInvalidation.remove(consumer);
      if (entry.consumers.size === 0 && subscriptions.get(changesRoot) === entry) {
        entry.dispose();
        subscriptions.delete(changesRoot);
        stopUnusedConnectionListener();
      }
    };
  }

  return {
    setEnabled(next) {
      if (next === enabled) return;
      enabled = next;
      retainedBeforeIdleReopen = undefined;
      if (!enabled) liveInvalidation.clear();
      else catchUpAfterEnable = true;
      subscriptions.forEach((entry) => {
        if (enabled) entry.start();
        else entry.dispose();
      });
    },
    subscribe({ channel, callback, params, meta }) {
      const targets = changeTargetsFromSubscribeParams(
        params,
        resourcesByList,
        resourcesByModel,
      );
      if (targets.length === 0) return noopSubscription;
      const disposers = targets.map(({ resource, changesRoot }) =>
        joinChangesRoot(changesRoot, resource, channel, callback,
          typeof params?.resource === "string"
            ? keys().data(meta?.dataProviderName ?? resource.schemaName)
              .resource(resource.identifier ?? params.resource).get()
            : undefined),
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

function liveChangeFromResult(
  data: unknown,
  changesRoot: string,
  resource: AngeeLiveResource,
): ChangeEvent {
  const event = recordValue(recordValue(data)?.[changesRoot]);
  const relatedRecords = Array.isArray(event?.relatedRecords)
    ? event.relatedRecords.flatMap((value) => {
      const record = recordValue(value);
      const model = stringValue(record?.model);
      const id = stringValue(record?.id);
      return model && id ? [{ model, id }] : [];
    })
    : [];
  return {
    model: stringValue(event?.model) ?? resource.modelLabel,
    id: stringValue(event?.id) || undefined,
    relatedRecords,
    action: stringValue(event?.action) ?? "*",
    changedFields: Array.isArray(event?.changedFields)
      ? event.changedFields.filter((field): field is string => typeof field === "string")
      : [],
    changedValues: recordValue(event?.changedValues) ?? {},
  };
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
  // multi-word ones to the camelCase keys `liveChangeFromResult` reads.
  return (
    `subscription angee_${root} { ` +
    `${root} { model id action relatedRecords: related_records { model id } ` +
    `changedFields: changed_fields changedValues: changed_values } }`
  );
}

function changeEvent(
  changes: readonly ChangeEvent[],
  channel: string,
  resource: AngeeLiveResource,
): LiveEvent {
  // No ids means model-wide to Refine consumers; a later row must not narrow it.
  const ids = changes.every((change) => change.id)
    ? [...new Set(changes.flatMap((change) => change.id ? [change.id] : []))]
    : undefined;
  const id = ids?.length === 1 ? ids[0] : undefined;
  const actions = new Set(changes.map((change) => change.action));
  const action = actions.size === 1 ? changes[0]?.action ?? "*" : "*";
  const relatedRecords = [...new Map(changes.flatMap((change) =>
    (change.relatedRecords ?? []).map((record) => [JSON.stringify([record.model, record.id]), record] as const),
  )).values()];
  return {
    channel,
    type: liveEventType(action),
    payload: {
      ...(ids ? { ids } : {}),
      ...(id ? { id } : {}),
      model: changes[0]?.model ?? resource.modelLabel,
      action,
      changedFields: [...new Set(changes.flatMap((change) => change.changedFields))],
      // Values from different records cannot describe a single row patch.
      changedValues: id || changes.length === 1
        ? changes.reduce<Record<string, unknown>>((values, change) => ({ ...values, ...change.changedValues }), {})
        : {},
      ...(relatedRecords.length > 0 ? { relatedRecords } : {}),
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

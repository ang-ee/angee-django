import { keys } from "@refinedev/core";
import { matchQuery, type Query } from "@tanstack/react-query";
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
  MetaQuery,
} from "@refinedev/core";
import { resourceMutationMeta, type ResourceMutationOperations, type ResourceMutationTarget } from "./operations";
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
import {
  createAuthoredLiveInvalidation,
  authoredQueryReadsLiveChange,
  authoredQueryReadsAnyModel,
  mutationReconciliation,
  type ReconciliationPublish,
  type AuthoredLiveChange,
} from "./query-invalidation";

type FetchFn = typeof globalThis.fetch;
export type GraphQLWsClient = ReturnType<typeof graphqlWS.createClient>;
const noopSubscription = () => undefined;
const SUBSCRIPTION_GRACE_MS = 2_000;

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
  queryClient?: AuthoredQueryInvalidationClient;
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
  /** Native Refine identity supplied by the metadata edge; standalone callers use the list root. */
  resourceIdentifier?: string;
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
  const authenticatedFetch = auth(baseFetch);
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
    hasGraphQLAnswer: Array.isArray(response?.errors) && response.errors.length > 0,
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
 * Retry errors without a GraphQL answer, except server errors and timeouts.
 * A 4xx transport response without a GraphQL body can retry once.
 */
export function retryableQueryError(error: unknown): boolean {
  const record = recordValue(error);
  const response = recordValue(record?.response);
  if (record?.hasGraphQLAnswer || Array.isArray(record?.graphQLErrors) && record.graphQLErrors.length > 0
    || Array.isArray(response?.errors) && response.errors.length > 0
    || typeof response?.status === "number" && response.status >= 500
    || record?.name === "AbortError" || record?.name === "TimeoutError") return false;
  return true;
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

type ChangeConsumer = (data: unknown) => void;

interface ChangeSubscription {
  dispose: () => void;
  start: () => void;
  consumers: Set<ChangeConsumer>;
  leaveTimer?: ReturnType<typeof setTimeout>;
}

export interface AngeeChangeLiveProvider extends LiveProvider {
  /** Suspend/advance acceptance lifetime on every false, retaining mounted consumers. */
  setEnabled: (enabled: boolean) => void;
}

type AuthoredQueryInvalidationClient = Pick<QueryClient, "invalidateQueries" | "getQueryCache">;

export function createAngeeChangeLiveProvider(
  client: Pick<GraphQLWsClient, "subscribe" | "on"> | undefined,
  resources: readonly AngeeLiveResource[],
  options: { queryClient?: AuthoredQueryInvalidationClient } = {},
): AngeeChangeLiveProvider {
  const resourcesByList = resourcesByListRoot(resources);
  const resourcesByModel = resourcesByModelLabel(resources);
  // graphql-ws does not dedup identical documents, so fan one upstream
  // subscription per changes root out to every mounted consumer — resource hooks
  // (useList/useOne, keyed by list root) and authored queries (keyed by model
  // label) alike — and retain the socket briefly after the last consumer
  // leaves for route handoffs.
  const subscriptions = new Map<string, ChangeSubscription>();
  let enabled = true;
  const liveInvalidation = options.queryClient
    ? createAuthoredLiveInvalidation(options.queryClient, nativeReadsChange)
    : undefined;

  function nativeReadsChange(query: Query, change: AuthoredLiveChange): boolean {
    return [change, ...(change.relatedRecords ?? [])].some((record) => {
      const resource = resourcesByModel.get(record.model);
      if (!resource?.roots.list) return false;
      const identifier = resource.resourceIdentifier ?? resource.roots.list;
      return [resource.schemaName, "default"].some((provider) => {
        const key = keys().data(provider).resource(identifier);
        if (!matchQuery({ queryKey: key.get() }, query)) return false;
        if (authoredQueryReadsAnyModel(query.meta, [record.model])) {
          return authoredQueryReadsLiveChange(query.meta, { ...change, model: record.model, id: record.id });
        }
        if (!record.id) return true;
        if (matchQuery({ queryKey: key.action("one").get() }, query)) {
          return matchQuery({ queryKey: key.action("one").id(record.id).get() }, query);
        }
        if (matchQuery({ queryKey: key.action("many").get() }, query)) {
          const ids = query.queryKey[key.action("many").get().length];
          return !Array.isArray(ids) || ids.includes(record.id);
        }
        // Lists/infinite scopes own membership and totals. A row cannot prove
        // exclusion from an arbitrary filter or ordering, so refetch the scope.
        return true;
      });
    });
  }

  const publish: ReconciliationPublish = () => undefined;
  if (liveInvalidation) publish[mutationReconciliation] = {
    get generation() { return liveInvalidation.generation; },
    reconcile: (request) => enabled ? liveInvalidation.reconcile(request) : Promise.resolve(),
    batch: (run) => enabled ? liveInvalidation.batch(run) : run(undefined),
  };

  // Attach one fan-in consumer to the shared upstream for `changesRoot`. Keep
  // its socket briefly after the last consumer leaves so a route handoff can
  // reuse the same ready registration.
  function joinChangesRoot(
    changesRoot: string,
    resource: AngeeLiveResource,
    channel: string,
    callback: (event: LiveEvent) => void,
  ): () => void {
    const consumer: ChangeConsumer = (data) => {
      const event = changeEventFromResult(data, changesRoot, channel, resource);
      if (event) callback(event);
    };
    const entry = subscriptions.get(changesRoot) ?? {
      dispose: noopSubscription,
      start: noopSubscription,
      consumers: new Set<ChangeConsumer>(),
    };
    if (entry.leaveTimer) {
      clearTimeout(entry.leaveTimer);
      entry.leaveTimer = undefined;
    }
    entry.consumers.add(consumer);
    if (!subscriptions.has(changesRoot)) {
      subscriptions.set(changesRoot, entry);
      entry.start = () => {
        if (!client) return;
        // Ignore late deliveries from a disposed subscription, even after resume.
        let active = true;
        const dispose = client.subscribe(
          { query: changeSubscriptionDocument(changesRoot) },
          {
            next: (result) => {
              if (!active || !enabled) return;
              const payload = recordValue(recordValue(result.data)?.[changesRoot]);
              // A ready closes this operation's registration or reconnect gap
              // for in-flight reads and retained snapshots without cancellation.
              if (payload?.action === "ready") {
                liveInvalidation?.ready(resource.modelLabel);
                return;
              }
              // One upstream result is one change, however many consumers share it.
              const deliver = () => entry.consumers.forEach((c) => c(result.data));
              if (liveInvalidation) liveInvalidation.push(liveChangeFromResult(result.data, changesRoot, resource), deliver);
              else deliver();
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
                subscriptions.delete(changesRoot);
              }
            },
            complete: () => undefined,
          },
        );
        entry.dispose = () => {
          if (active) {
            active = false;
            dispose();
          }
        };
      };
      if (enabled) entry.start();
    }
    return () => {
      entry.consumers.delete(consumer);
      if (entry.consumers.size === 0 && subscriptions.get(changesRoot) === entry) {
        if (!enabled) {
          entry.dispose();
          subscriptions.delete(changesRoot);
          return;
        }
        entry.leaveTimer = setTimeout(() => {
          entry.leaveTimer = undefined;
          if (entry.consumers.size !== 0 || subscriptions.get(changesRoot) !== entry) return;
          entry.dispose();
          subscriptions.delete(changesRoot);
        }, SUBSCRIPTION_GRACE_MS);
      }
    };
  }

  return {
    publish,
    setEnabled(next) {
      // Suspension is an acceptance-lifetime boundary even if no websocket is
      // configured, or another transition already suspended delivery.
      if (!next) liveInvalidation?.clear();
      if (next === enabled) return;
      enabled = next;
      subscriptions.forEach((entry, root) => {
        if (enabled) {
          if (entry.consumers.size > 0) entry.start();
        } else {
          if (entry.leaveTimer) clearTimeout(entry.leaveTimer);
          entry.leaveTimer = undefined;
          entry.dispose();
          if (entry.consumers.size === 0) subscriptions.delete(root);
        }
      });
    },
    subscribe({ channel, callback, params }) {
      if (!client) return noopSubscription;
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

function liveChangeFromResult(
  data: unknown,
  changesRoot: string,
  resource: AngeeLiveResource,
): AuthoredLiveChange {
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
    action: stringValue(event?.action) ?? undefined,
    relatedRecords,
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
  // multi-word ones to the camelCase keys `changeEventFromResult` reads.
  return (
    `subscription angee_${root} { ` +
    `${root} { model id action relatedRecords: related_records { model id } ` +
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
      ...(Array.isArray(event?.relatedRecords) && event.relatedRecords.length > 0
        ? { relatedRecords: event.relatedRecords }
        : {}),
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

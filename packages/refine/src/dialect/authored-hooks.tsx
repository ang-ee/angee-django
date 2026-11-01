import { useCallback, useRef } from "react";
import {
  useCustomMutation,
  useDataProvider,
  useSubscription,
  type BaseRecord,
  type HttpError,
  type MetaQuery,
  type UseCustomMutationProps,
  type UseCustomMutationReturnType,
} from "@refinedev/core";
import {
  useQuery,
  useQueries,
  useQueryClient,
  type UseQueryResult,
  type Query,
} from "@tanstack/react-query";

import {
  useAcceptQueryData,
  invalidateAuthoredQueries,
  useReconcileMutationResponse,
  type MutationReconciliationOptions,
  type MutationExecutionOptions,
} from "../query-invalidation";
import {
  useStableArray,
  useStableValue,
} from "../stable-deps";
import type {
  DocumentData,
  DocumentVariables,
  TypedDocumentNode,
} from "../typed-document";
import { useActiveDataProviderName } from "./data-provider-context";
import { authoredOperationData, mutationMeta } from "./wire";
import { authoredQueryKey, authoredQueryOptions, useAuthoredErrorPolicy } from "./authored-query-options";
export { authoredQueryKey, authoredQueryOptions } from "./authored-query-options";
export { authoredOperationData } from "./wire";

/** Any authored (non-CRUD) GraphQL operation: a generated `TypedDocumentNode`. */
export type AuthoredDocument = TypedDocumentNode<
  unknown,
  never
>;
/**
 * The variables an authored document takes, or `Record<string, never>` when it
 * takes none — the parameter type the authored hooks require and the type a
 * caller composing them (e.g. a source that maps its own input to a document's
 * variables) declares, so the variables stay pinned to the document.
 */
export type AuthoredVariables<TDocument extends AuthoredDocument> =
  DocumentVariables<TDocument> extends Record<string, unknown>
    ? DocumentVariables<TDocument>
    : Record<string, never>;

export interface AuthoredOperationOptions {
  /** Refine data provider name; defaults to the active Angee layout schema. */
  dataProviderName?: string;
}

export interface AuthoredQueryOptions extends AuthoredOperationOptions {
  enabled?: boolean;
  /**
   * `false` turns off refine's generic error notification, for a read whose
   * surface renders the failure itself (an error banner in place of the record).
   * The auth policy still runs and the query still reports its error.
   */
  errorNotification?: false;
  /** Retain the last result while a new variable set loads, using native Query placeholders. */
  keepPreviousData?: boolean;
  /** Exact rows whose live changes invalidate this read; omitted keeps model-wide semantics. */
  records?: readonly { model: string; id: string }[];
  /** Models whose changes match only through their event's related records. */
  relatedModels?: readonly string[];
  /** Native TanStack Query polling policy for durable external operations. */
  refetchInterval?: number | false | ((query: Pick<Query<unknown, Error>, "state">) => number | false | undefined);
  /**
   * Exact canonical model labels this bespoke read depends on; local writes and
   * live changes refetch it. This metadata-free package does no alias resolution:
   * a rendered caller accepts friendly spellings only by canonicalizing at its
   * `@angee/metadata` edge before calling this hook.
   */
  models?: readonly string[];
}

/** One authored read in a dynamic batch, addressed by a caller-stable key. */
export interface AuthoredQueryBatchScope<TDocument extends AuthoredDocument>
  extends Pick<AuthoredQueryOptions, "models" | "records" | "relatedModels"> {
  /** Result label only; document/provider/variables own query identity. */
  key: string;
  document: TDocument;
  variables?: AuthoredVariables<TDocument>;
}

export function useAuthoredQuery<TDocument extends AuthoredDocument>(
  document: TDocument,
  variables?: AuthoredVariables<TDocument>,
  options: AuthoredQueryOptions = {},
): UseQueryResult<DocumentData<TDocument>, Error> {
  const activeProvider = useActiveDataProviderName();
  const provider = options.dataProviderName ?? activeProvider ?? "default";
  const dataProvider = useDataProvider();
  const client = useQueryClient();
  const models = useStableArray(options.models ?? []);
  const records = useStableValue(options.records, []);
  const relatedModels = useStableArray(options.relatedModels ?? []);
  const configured = authoredQueryOptions(
    client, dataProvider, provider, document, variables, models, records, relatedModels, options.keepPreviousData,
  );
  const result = useQuery({
    ...configured,
    enabled: options.enabled ?? true,
    refetchInterval: options.refetchInterval,
  });
  useAuthoredLiveInterest(options.enabled ?? true, models);
  useAuthoredErrorPolicy([configured.queryKey], { notify: options.errorNotification !== false });
  return result;
}

/** Native query results addressed by a domain label; labels never key the cache. */
export function useAuthoredQueryBatch<TDocument extends AuthoredDocument>(
  scopes: readonly AuthoredQueryBatchScope<TDocument>[],
  options: AuthoredOperationOptions & Pick<AuthoredQueryOptions, "enabled" | "errorNotification" | "refetchInterval"> = {},
): ReadonlyMap<string, UseQueryResult<DocumentData<TDocument>, Error>> {
  const activeProvider = useActiveDataProviderName();
  const provider = options.dataProviderName ?? activeProvider ?? "default";
  const dataProvider = useDataProvider();
  const client = useQueryClient();
  const configured = scopes.map((scope) => authoredQueryOptions(
    client, dataProvider, provider, scope.document, scope.variables, scope.models, scope.records, scope.relatedModels,
  ));
  // Labels address results; only native provider/document/variables identity owns
  // requests. A dynamic batch may contain several labels for the same request.
  const hashes = configured.map((query) => client.defaultQueryOptions(query).queryHash);
  const unique = new Map(hashes.map((hash, index) => [hash, configured[index]!]));
  const indexByHash = new Map([...unique.keys()].map((hash, index) => [hash, index]));
  const queries = [...unique.values()].map((query) => ({
    ...query,
    enabled: options.enabled ?? true,
    refetchInterval: options.refetchInterval,
  }));
  const results = useQueries({ queries });
  const models = useStableArray([...new Set(scopes.flatMap((scope) => scope.models ?? []))].sort());
  useAuthoredLiveInterest(options.enabled ?? true, models);
  useAuthoredErrorPolicy(queries.map((query) => query.queryKey), { notify: options.errorNotification !== false });
  return new Map(scopes.map((scope, index) => [scope.key, results[indexByHash.get(hashes[index]!)!]!]));
}

/** Invalidate every authored read registered against any supplied model. */
export function useInvalidateAuthoredModels(): (
  modelLabels: readonly string[],
) => void {
  const queryClient = useQueryClient();
  return useCallback((modelLabels: readonly string[]) => {
    void invalidateAuthoredQueries(queryClient, modelLabels);
  }, [queryClient]);
}

/** Cancel older snapshots before accepting a complete typed result at its exact native key. */
export function useSetAuthoredQueryData(): <TDocument extends AuthoredDocument>(
  document: TDocument,
  variables: AuthoredVariables<TDocument> | undefined,
  data: DocumentData<TDocument> | ((current: DocumentData<TDocument> | undefined) => DocumentData<TDocument> | undefined),
  options?: AuthoredOperationOptions & { complete?: boolean },
) => Promise<void> {
  const accept = useAcceptQueryData();
  const activeProvider = useActiveDataProviderName();
  return useCallback(async (document, variables, data, options) => {
    const queryKey = authoredQueryKey(document, variables, options?.dataProviderName ?? activeProvider ?? "default");
    await accept(queryKey, data, { complete: options?.complete });
  }, [activeProvider, accept]);
}

/** Optional execution metadata explicitly joins one command's reconciliation collector. */
export type AuthoredMutate<TDocument extends AuthoredDocument> = (
  variables?: AuthoredVariables<TDocument>,
  execution?: MutationExecutionOptions,
) => Promise<DocumentData<TDocument> | undefined>;

export interface AuthoredMutationOptions<
  TData = unknown,
  TVariables = Record<string, unknown>,
> extends AuthoredOperationOptions, MutationReconciliationOptions<TData | undefined, TVariables> {
  /**
   * For operations whose result is a secret shown once: observer reset on
   * settlement; the cache entry is collected on the next tick; single-flight
   * per hook instance. Overlapping calls reject before executing.
   */
  transient?: boolean;
  /**
   * `false` turns off refine's generic error notification, for a caller that
   * renders the failure itself (a callback page, a form's own error). The
   * mutation still rejects with the error.
   */
  errorNotification?: false;
  /**
   * Exact canonical model labels whose registered reads should refetch after
   * success. This metadata-free package exact-matches the strings; callers that
   * accept aliases canonicalize them before this boundary.
   */
  invalidateModels?: readonly string[];
  /**
   * Explicit response-backed completion: accept authoritative returned values at
   * their native query owner before resolving, then refresh derived reads in the
   * background. Omit to retain completion after awaited refresh. Rejected reads
   * remain native Query errors and follow the existing read error policy.
   */
  acceptResponse?: (data: TData | undefined, variables: TVariables) => void | Promise<void>;
  /** Optional domain-level success guard before invalidating registered reads. */
  shouldInvalidate?: (data: TData | undefined, variables: TVariables) => boolean;
  /**
   * Extract a domain result envelope from successful GraphQL transport data.
   * If it carries `{ error_code, error }`, the hook throws before invalidating
   * reads so callers do not each re-implement the same result gating.
   */
  errorFrom?: (
    data: TData | undefined,
    variables: TVariables,
  ) => AuthoredMutationEnvelope | Error | string | null | undefined;
}

export function useAuthoredMutation<TDocument extends AuthoredDocument>(
  document: TDocument,
  options: AuthoredMutationOptions<
    DocumentData<TDocument>,
    AuthoredVariables<TDocument>
  > = {},
): [AuthoredMutate<TDocument>, { fetching: boolean; error: Error | null; reset: () => void }] {
  type Data = DocumentData<TDocument>;
  type Variables = AuthoredVariables<TDocument>;
  const activeDataProviderName = useActiveDataProviderName();
  const dataProviderName = options.dataProviderName ?? activeDataProviderName ?? "default";
  const invalidateModelLabels = useStableArray(options.invalidateModels ?? []);
  const invalidates = options.invalidates;
  const records = options.records;
  const reconcileMutationResponse = useReconcileMutationResponse();
  const shouldInvalidate = options.shouldInvalidate;
  const errorFrom = options.errorFrom;
  const acceptResponse = options.acceptResponse;
  return useOperationMutation<Data, Variables>({
    transient: options.transient,
    errorNotification: options.errorNotification,
    request: (variables) => ({
      url: "", method: "post", dataProviderName, meta: mutationMeta(document, variables),
    }),
    select: authoredOperationData<Data>,
    onResponse: async (data, variables, execution) => {
      const resultError = errorFromAuthoredEnvelope(errorFrom?.(data, variables));
      if (resultError) throw resultError;
      const effects = shouldInvalidate?.(data, variables) ?? true;
      await reconcileMutationResponse(data, variables, {
        ...execution,
        acceptResponse,
        ...(effects ? { records, invalidateModels: invalidateModelLabels, invalidates } : {}),
      });
    },
  });
}

type OperationMutationOptions<Data, Variables extends Record<string, unknown>> = {
  request: (variables: Variables) => Omit<Parameters<UseCustomMutationReturnType<BaseRecord, HttpError, Variables>["mutateAsync"]>[0], "values">;
  select: (data: BaseRecord) => Data | undefined;
  onResponse: (data: Data | undefined, variables: Variables, execution?: MutationExecutionOptions) => Promise<void>;
} & Pick<AuthoredMutationOptions<Data, Variables>, "transient" | "errorNotification">;

const operationExecution = Symbol("angee.operationExecution");
type ExecutionMeta = MetaQuery & { [operationExecution]?: MutationExecutionOptions };

/** One native execution/retention owner for authored operations and generated actions. */
export function useOperationMutation<Data, Variables extends Record<string, unknown>>(
  options: OperationMutationOptions<Data, Variables>,
): [(variables?: Variables, execution?: MutationExecutionOptions) => Promise<Data | undefined>, { fetching: boolean; error: Error | null; reset: () => void }] {
  const { request, select, onResponse } = options;
  const inFlight = useRef({ count: 0, transient: false });
  type MutationContext = { select: typeof select; onResponse: typeof onResponse; execution?: MutationExecutionOptions };
  const captureMutationContext = (input: { meta?: MetaQuery }): MutationContext => ({
    select, onResponse, execution: (input.meta as ExecutionMeta | undefined)?.[operationExecution],
  });
  const mutationOptions: NonNullable<UseCustomMutationProps<BaseRecord, HttpError, Variables>["mutationOptions"]> = {
    onMutate: captureMutationContext,
    async onSuccess(response, { values }, context) {
      const captured = context as MutationContext;
      try { await captured.onResponse(captured.select(response.data), values, captured.execution); }
      finally { delete captured.execution; }
    },
    onSettled(_response, _error, input, context) {
      if (input.meta) delete (input.meta as ExecutionMeta)[operationExecution];
      if (context) delete (context as MutationContext).execution;
    },
  };
  // Native observers retain a fixed policy: changing the next dispatch's mode
  // must neither extend a pending secret's GC nor shorten an ordinary call's GC.
  const ordinaryRun = useCustomMutation<BaseRecord, HttpError, Variables>({ mutationOptions });
  const transientRun = useCustomMutation<BaseRecord, HttpError, Variables>({
    mutationOptions: { ...mutationOptions, gcTime: 0 },
  });
  const lastTransient = useRef(false);
  // Stable identity: chat runtimes and other long-lived effects may depend on
  // authored mutations, while refine can churn `mutateAsync` across renders.
  // Read the latest execution context at call time so consumers do not reconnect
  // or restart work just because the hook rerendered.
  const mutationRef = useRef({
    request,
    select,
    ordinaryRun,
    transientRun,
    transient: options.transient,
    errorNotification: options.errorNotification,
  });
  mutationRef.current = {
    request,
    select,
    ordinaryRun,
    transientRun,
    transient: options.transient,
    errorNotification: options.errorNotification,
  };
  const mutate = useCallback<(variables?: Variables, execution?: MutationExecutionOptions) => Promise<Data | undefined>>(async (variables, execution) => {
    const {
      request,
      select,
      ordinaryRun,
      transientRun,
      transient,
      errorNotification,
    } = mutationRef.current;
    // Guard synchronously, before native MutationObserver creates an operation.
    // Also protect a transient operation if the next render changes its policy.
    if (inFlight.current.count > 0 && (transient || inFlight.current.transient)) {
      throw new Error("Transient mutations cannot overlap another mutation.");
    }
    const run = transient ? transientRun : ordinaryRun;
    lastTransient.current = transient ?? false;
    inFlight.current.count += 1;
    if (transient) inFlight.current.transient = true;
    const resolvedVariables = (variables ?? {}) as Variables;
    try {
      const operation = request(resolvedVariables);
      const response = await run.mutateAsync({
        ...operation,
        ...(execution ? { meta: { ...operation.meta, [operationExecution]: execution } } : {}),
        values: resolvedVariables,
        ...(errorNotification === false ? { errorNotification } : {}),
      });
      return select(response.data);
    } finally {
      if (transient) {
        run.mutation.reset();
        inFlight.current.transient = false;
      }
      inFlight.current.count -= 1;
    }
  }, []);
  const reset = useCallback(() => {
    const { ordinaryRun, transientRun } = mutationRef.current;
    (lastTransient.current ? transientRun : ordinaryRun).mutation.reset();
  }, []);
  const run = lastTransient.current ? transientRun : ordinaryRun;
  return [
    mutate,
    {
      fetching: run.mutation.isPending,
      error: run.mutation.error as Error | null,
      reset,
    },
  ];
}

export interface AuthoredMutationEnvelope {
  error?: unknown;
  error_code?: unknown;
}

export function errorFromAuthoredEnvelope(
  value: AuthoredMutationEnvelope | Error | string | null | undefined,
): Error | null {
  if (!value) return null;
  if (value instanceof Error) return value;
  if (typeof value === "string") return value ? new Error(value) : null;
  if (!value.error_code) return null;
  const message = typeof value.error === "string" && value.error
    ? value.error
    : String(value.error_code);
  return new Error(message);
}

/** Live invalidation rides the provider's change consumer, so the hook's own event handler is a noop. */
const NO_LIVE_EVENT = (): void => undefined;

/** A stable per-model-set channel for the authored read's live subscription. */
function authoredLiveChannel(models: readonly string[]): string {
  return `angee/authored/${models.join(",")}`;
}

export function useAuthoredLiveInterest(
  enabled: boolean,
  models: readonly string[],
): void {
  // An authored read is invisible to the live bridge unless it declares interest:
  // a page built only from authored queries would open no socket and never go
  // live. Register each read model so its changes root is subscribed through the
  // shared fan-out (one upstream per root, joined with any resource hook watching
  // the same model) and torn down with the hook. refine's useSubscription owns
  // the lifecycle and no-ops when the app has no live provider; the provider's
  // consumer invalidates this read on each change, so onLiveEvent stays a noop.
  useSubscription({
    channel: authoredLiveChannel(models),
    params: { models },
    types: ["*"],
    enabled: enabled && models.length > 0,
    onLiveEvent: NO_LIVE_EVENT,
  });
}

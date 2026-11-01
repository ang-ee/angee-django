import { useCallback, useMemo } from "react";
import { keys, pickDataProvider, useInvalidate, usePublish, useResourceParams, type LiveProvider } from "@refinedev/core";
import { CancelledError, matchQuery, useQueryClient, type Query, type QueryClient, type QueryKey, type Updater } from "@tanstack/react-query";

import { recordValue } from "./dialect/wire";

/**
 * Exact-match authored query metadata against canonical model labels supplied by
 * the caller; this metadata-free layer deliberately performs no alias mapping.
 */
export function authoredQueryReadsAnyModel(
  meta: unknown,
  modelLabels: readonly string[],
): boolean {
  const models = recordValue(meta)?.angeeModels;
  if (!Array.isArray(models)) return false;
  const wanted = new Set(modelLabels);
  return models.some((model) => typeof model === "string" && wanted.has(model));
}

/** Match one live row change, respecting an authored query's optional exact-record interests. */
export function authoredQueryReadsChange(meta: unknown, model: string, id: string): boolean {
  if (!authoredQueryReadsAnyModel(meta, [model])) return false;
  const metadata = recordValue(meta);
  const records = metadata?.angeeRecords;
  const broadModels = metadata?.angeeBroadModels;
  if (Array.isArray(broadModels) && broadModels.includes(model)) return true;
  const relatedModels = metadata?.angeeRelatedModels;
  if (Array.isArray(relatedModels) && relatedModels.includes(model)) return false;
  if (!Array.isArray(records)) return true;
  return records.some((value) => {
    const record = recordValue(value);
    return record?.model === model && record?.id === id;
  });
}

/** Match one live change: an exact row with its related rows, or a whole model. */
function authoredQueryReadsLiveChange(meta: unknown, change: AuthoredLiveChange): boolean {
  if (!change.id) return authoredQueryReadsAnyModel(meta, [change.model]);
  return authoredQueryReadsChange(meta, change.model, change.id)
    || authoredQueryReadsAnyModel(meta, [change.model]) && (change.relatedRecords ?? []).some((record) => {
      const records = recordValue(meta)?.angeeRecords;
      return Array.isArray(records) && records.some((value) => {
        const interest = recordValue(value);
        return interest?.model === record.model && interest?.id === record.id;
      });
    });
}

/** Refetch every active authored read registered against one of the moved models. */
export async function invalidateAuthoredQueries(
  queryClient: Pick<QueryClient, "cancelQueries" | "invalidateQueries">,
  modelLabels: readonly string[],
): Promise<void> {
  return invalidateAuthoredQueriesMatching(
    queryClient,
    (query) => authoredQueryReadsAnyModel(query.meta, modelLabels),
  );
}

/** One live change: an exact row with its related rows, or a whole model when ``id`` is absent. */
export interface AuthoredLiveChange {
  model: string;
  id?: string;
  /** Transport operation, when known; never a row revision. */
  action?: string;
  relatedRecords?: readonly { model: string; id: string }[];
}

/** Quiet period that closes a burst of live changes. */
const LIVE_WINDOW_MS = 300;
/** Longest a continuous stream may defer its first buffered change. */
const LIVE_MAX_WAIT_MS = 2000;

export interface QueryReconciliation {
  changes: readonly AuthoredLiveChange[];
  /** Native resource keys remain a distinct adapter from authored metadata. */
  predicate?: (query: Query) => boolean;
  /** Only complete projections actually accepted by this response are excluded. */
  accepted?: ReadonlySet<string>;
}

export interface AuthoredLiveInvalidation {
  push: (change: AuthoredLiveChange, deliver?: () => void) => void;
  reconcile: (request: QueryReconciliation) => Promise<void>;
  batch: <T>(run: () => Promise<T>) => Promise<T>;
  clear: () => void;
  readonly generation: number;
}

/** Provider-owned native publish extension; never a global registry or event sent upstream. */
export const mutationReconciliation = Symbol("angee.mutationReconciliation");
export type ReconciliationPublish = NonNullable<LiveProvider["publish"]> & {
  [mutationReconciliation]?: Pick<AuthoredLiveInvalidation, "reconcile" | "batch" | "generation">;
};

/**
 * One provider-owned burst for accepted local effects and server events. Cancel
 * earlier snapshots immediately; retain committed data, honestly stale, until
 * the bounded flush. Query owns retries, errors and all row state.
 */
export function createAuthoredLiveInvalidation(
  queryClient: Pick<QueryClient, "cancelQueries" | "invalidateQueries" | "getQueryCache">,
  nativeReadsChange: (query: Query, change: AuthoredLiveChange) => boolean = () => false,
): AuthoredLiveInvalidation {
  let requests: QueryReconciliation[] = [];
  let deliveries: (() => void)[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;
  let firstAt: number | undefined;
  let batchDepth = 0;
  let generation = 0;
  const pending = new Set<{ resolve: () => void; reject: (error: unknown) => void }>();
  let waiting: { resolve: () => void; reject: (error: unknown) => void }[] = [];
  const matches = (query: Query, request: QueryReconciliation) =>
    !request.accepted?.has(query.queryHash) && (
      request.predicate?.(query) || request.changes.some((change) =>
        authoredQueryReadsLiveChange(query.meta, change) || nativeReadsChange(query, change))
    );

  async function flush(): Promise<void> {
    if (batchDepth > 0 || requests.length === 0) return;
    if (timer !== undefined) clearTimeout(timer);
    const settled = waiting;
    waiting = [];
    const batch = requests;
    const callbacks = deliveries;
    requests = [];
    deliveries = [];
    timer = undefined;
    firstAt = undefined;
    const predicate = (query: Query) => batch.some((request) => matches(query, request));
    // The native infinite query function consumes this batch during its own
    // fetch lifecycle. Inactive reads discard it and perform a full read later.
    const queries = queryClient.getQueryCache().findAll({ predicate });
    const changesByQuery = new Map(queries.map((query) => {
      const effects = batch.filter((request) => matches(query, request)).flatMap((request) => request.changes)
        .filter((change) => authoredQueryReadsLiveChange(query.meta, change));
      const changes = [...new Map(effects.map((change) => [JSON.stringify(change), change])).values()];
      if (query.meta) query.meta.angeeChanges = changes;
      return [query, changes] as const;
    }));
    const refresh = queryClient.invalidateQueries({ predicate, type: "all", refetchType: "active" });
    // Stock Refine auto callbacks join the running native request. createApp
    // uses manual mode, leaving scheduling entirely with this owner.
    try {
      callbacks.forEach((deliver) => deliver());
      await refresh;
      settled.forEach((waiter) => { pending.delete(waiter); waiter.resolve(); });
    } catch (error) {
      settled.forEach((waiter) => { pending.delete(waiter); waiter.reject(error); });
      throw error;
    } finally {
      for (const query of queries) {
        const meta = query.meta;
        if (meta && meta.angeeChanges === changesByQuery.get(query)) delete meta.angeeChanges;
      }
    }
  }

  function reconcile(request: QueryReconciliation): Promise<void> {
    const predicate = (query: Query) => matches(query, request);
    void queryClient.cancelQueries({ predicate: (query) => query.state.fetchStatus !== "idle" && predicate(query) });
    void queryClient.invalidateQueries({ predicate, type: "all", refetchType: "none" });
    requests.push(request);
    const now = performance.now();
    firstAt ??= now;
    if (timer !== undefined) clearTimeout(timer);
    if (batchDepth > 0) return Promise.resolve();
    timer = setTimeout(() => { void flush().catch(() => undefined); }, Math.max(0, Math.min(LIVE_WINDOW_MS, firstAt + LIVE_MAX_WAIT_MS - now)));
    return new Promise<void>((resolve, reject) => {
      const waiter = { resolve, reject };
      waiting.push(waiter);
      pending.add(waiter);
    });
  }

  return {
    get generation() { return generation; },
    clear() {
      generation++;
      batchDepth = 0;
      if (timer !== undefined) clearTimeout(timer);
      requests = [];
      deliveries = [];
      timer = undefined;
      firstAt = undefined;
      // Authentication suspension drops old-actor work and settles its waiters.
      pending.forEach(({ resolve }) => resolve());
      pending.clear();
      waiting = [];
    },
    async batch<T>(run: () => Promise<T>): Promise<T> {
      const started = generation;
      batchDepth++;
      let failed = false;
      try {
        return await run();
      } catch (error) {
        failed = true;
        throw error;
      } finally {
        if (started === generation) {
          batchDepth--;
          if (batchDepth === 0) {
            try { await flush(); }
            catch (error) { if (!failed) throw error; }
          }
        }
      }
    },
    reconcile,
    push(change, deliver) {
      if (deliver) deliveries.push(deliver);
      void reconcile({ changes: [change] }).catch(() => undefined);
    },
  };
}

/** Imperative exact acceptance; caller owns its actor/lifecycle scope. Hooks use useAcceptQueryData. */
export function acceptQueryData<TData>(
  client: QueryClient,
  queryKey: QueryKey,
  data: Updater<TData | undefined, TData | undefined>,
  options: { complete?: boolean } = {},
): Promise<void> {
  return acceptScopedQueryData(client, queryKey, data, options, () => true);
}

async function acceptScopedQueryData<TData>(
  client: QueryClient,
  queryKey: QueryKey,
  data: Updater<TData | undefined, TData | undefined>,
  { complete = true }: { complete?: boolean },
  isCurrent: () => boolean,
): Promise<void> {
  if (!isCurrent()) throw new CancelledError({ silent: true });
  await client.cancelQueries({ queryKey, exact: true });
  if (!isCurrent()) throw new CancelledError({ silent: true });
  let adopted = false;
  client.setQueryData<TData>(queryKey, (current) => {
    const next = typeof data === "function"
      ? (data as (current: TData | undefined) => TData | undefined)(current)
      : data;
    adopted = next !== undefined && next !== current;
    return adopted ? next : undefined;
  });
  if (adopted) {
    const query = client.getQueryCache().find({ queryKey, exact: true });
    if (query) query.setOptions({ ...query.options, meta: {
      ...query.meta,
      angeeAcceptedResponse: { complete, dataUpdateCount: query.state.dataUpdateCount },
    } });
  }
  if (!complete) await client.invalidateQueries({ queryKey, exact: true, refetchType: "none" });
}

/** Capture the originating render's actor generation, including delayed callbacks. */
export function useAcceptQueryData() {
  const client = useQueryClient();
  const publish = usePublish() as ReconciliationPublish | undefined;
  const generation = publish?.[mutationReconciliation]?.generation;
  return useCallback(<TData,>(
    queryKey: QueryKey,
    data: Updater<TData | undefined, TData | undefined>,
    options: { complete?: boolean } = {},
  ): Promise<void> => acceptScopedQueryData(client, queryKey, data, options,
    () => generation === publish?.[mutationReconciliation]?.generation),
  [client, publish, generation]);
}

type NativeInvalidation = Parameters<ReturnType<typeof useInvalidate>>[0];
type Effects<TData, TVariables, TValue> = readonly TValue[] | ((data: TData, variables: TVariables) => readonly TValue[]);
export interface MutationReconciliationOptions<TData, TVariables> {
  acceptResponse?: (data: TData, variables: TVariables) => void | Promise<void>;
  records?: Effects<TData, TVariables, AuthoredLiveChange>;
  invalidateModels?: readonly string[];
  invalidates?: Effects<TData, TVariables, NativeInvalidation>;
}

function effectValues<TData, TVariables, TValue>(
  effect: Effects<TData, TVariables, TValue> | undefined, data: TData, variables: TVariables,
): readonly TValue[] {
  return typeof effect === "function" ? effect(data, variables) : effect ?? [];
}

/** Shared action/authored/CRUD completion; default callers still await refresh. */
export function useReconcileMutationResponse() {
  const client = useQueryClient();
  const invalidate = useInvalidate();
  const publish = usePublish() as ReconciliationPublish | undefined;
  const { resources } = useResourceParams();
  const generation = publish?.[mutationReconciliation]?.generation;
  const reconcile = useCallback(async <TData, TVariables>(
    data: TData,
    variables: TVariables,
    options: MutationReconciliationOptions<TData, TVariables>,
  ): Promise<void> => {
    // A captured mutation completion belongs to the actor that started it.
    if (generation !== publish?.[mutationReconciliation]?.generation) return;
    const records = effectValues(options.records, data, variables);
    const changes = [...records, ...(options.invalidateModels ?? [])
      .filter((model) => !records.some((record) => record.model === model))
      .map((model) => ({ model }))];
    const targets = effectValues(options.invalidates, data, variables);
    const accepted = new Set<string>();
    if (options.acceptResponse) {
      const before = new Map(client.getQueryCache().getAll().map((query) => [query.queryHash, query.meta?.angeeAcceptedResponse]));
      await options.acceptResponse(data, variables);
      if (generation !== publish?.[mutationReconciliation]?.generation) return;
      client.getQueryCache().getAll().forEach((query) => {
        const projection = recordValue(query.meta?.angeeAcceptedResponse);
        if (projection?.complete === true && !query.state.isInvalidated
          && projection !== before.get(query.queryHash)
          && projection.dataUpdateCount === query.state.dataUpdateCount) accepted.add(query.queryHash);
      });
    }
    // Explicit native policies retain useInvalidate's full selector/options
    // contract; these are deliberate caller refreshes, not generic live effects.
    const customTargets = targets.filter((target) => target.invalidationFilters || target.invalidationOptions);
    const filters = targets.filter((target) => !target.invalidationFilters && !target.invalidationOptions).flatMap((target) => {
      if (target.invalidates === false) return [];
      const provider = pickDataProvider(target.resource, target.dataProviderName, resources);
      const key = keys().data(provider).resource(target.resource ?? "");
      return target.invalidates.flatMap((kind) => {
        if (kind === "all") return [{ queryKey: keys().data(provider).get() }];
        if (kind === "resourceAll") return [{ queryKey: key.get() }];
        if (kind === "detail") return [{ queryKey: key.action("one").id(target.id ?? "").get() }];
        if (kind === "list") return [{ queryKey: key.action("list").get() }, { queryKey: key.action("infinite").get() }];
        if (kind === "many") return [{ queryKey: key.action("many").get() }];
        return [];
      });
    });
    if (changes.length === 0 && filters.length === 0 && customTargets.length === 0) return;
    const predicate = (query: Query) => filters.some((filter) => matchQuery(filter, query));
    const request = { changes, predicate, accepted };
    const scheduler = publish?.[mutationReconciliation];
    if (scheduler) {
      const refresh = Promise.all([
        ...(changes.length || filters.length ? [scheduler.reconcile(request)] : []),
        ...customTargets.map((target) => invalidate(target)),
      ]);
      if (options.acceptResponse) void refresh.catch(() => undefined);
      else await refresh;
      return;
    }
    const refresh = (async () => {
      if (options.acceptResponse) {
        await Promise.all([
          ...customTargets.map((target) => invalidate(target)),
          invalidateAuthoredQueriesMatching(client, (query) => !accepted.has(query.queryHash)
            && (predicate(query) || changes.some((change) => authoredQueryReadsLiveChange(query.meta, change)))),
        ]);
      } else {
        await Promise.all([
          ...targets.map((target) => invalidate(target)),
          invalidateAuthoredQueriesMatching(client, (query) => changes.some((change) => authoredQueryReadsLiveChange(query.meta, change))),
        ]);
      }
    })();
    if (options.acceptResponse) void refresh.catch(() => undefined);
    else await refresh;
  }, [client, invalidate, publish, resources, generation]);
  return useMemo(() => Object.assign(reconcile, {
    /** Explicit bulk command boundary; partial failures still flush in finally. */
    batch: async <T,>(run: () => Promise<T>): Promise<T> => {
      const scheduler = publish?.[mutationReconciliation];
      return scheduler ? scheduler.batch(run) : run();
    },
  }), [reconcile, publish]);
}

/**
 * The cancelling protocol shared by model-wide, exact-row and live invalidation
 * (mutations included). Subscription readiness enters the same provider-owned
 * scheduler as an authoritative model-wide recovery effect.
 */
async function invalidateAuthoredQueriesMatching(
  queryClient: Pick<QueryClient, "cancelQueries" | "invalidateQueries">,
  predicate: (query: Query) => boolean,
): Promise<void> {
  // Native invalidation joins an initial in-flight request instead of restarting
  // it. Cancel that snapshot first so an event cannot be lost when it settles.
  await queryClient.cancelQueries({
    predicate: (query) => predicate(query)
      && query.state.data === undefined
      && query.state.fetchStatus !== "idle",
  });
  return queryClient.invalidateQueries({
    predicate,
    type: "all",
    refetchType: "active",
  });
}

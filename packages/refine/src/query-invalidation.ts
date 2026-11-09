import type { Query, QueryClient, QueryKey } from "@tanstack/react-query";

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
function authoredQueryReadsLiveChange(meta: unknown, change: LiveChange): boolean {
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

/**
 * Refetch every active authored read registered against one of the models a
 * caller's own write moved: a read that started before the write never commits.
 * Live changes from other writers coalesce without cancelling
 * (`createLiveInvalidation`); connection catch-up
 * (`catchUpAuthoredQueries`) is the separate non-cancelling protocol.
 */
export async function invalidateAuthoredQueries(
  queryClient: Pick<QueryClient, "cancelQueries" | "invalidateQueries">,
  modelLabels: readonly string[],
): Promise<void> {
  const predicate = (query: Query) => authoredQueryReadsAnyModel(query.meta, modelLabels);
  // Native invalidation joins an initial in-flight request instead of restarting
  // it. Cancel that snapshot first so the write cannot be lost when it settles.
  await queryClient.cancelQueries({
    predicate: (query) => predicate(query)
      && query.state.data === undefined
      && query.state.fetchStatus !== "idle",
  });
  return queryClient.invalidateQueries({ predicate, type: "all", refetchType: "active" });
}

/** Catch up retained authored reads; a retry restarts running data-holding refetches. */
export async function catchUpAuthoredQueries(
  queryClient: Pick<QueryClient, "invalidateQueries">,
  modelLabels: readonly string[],
  retainedAtStart: ReadonlySet<string> | undefined,
  cancelRefetch: boolean,
): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => authoredQueryWithData(query, modelLabels)
      && (retainedAtStart === undefined || retainedAtStart.has(query.queryHash)),
    type: "all",
    refetchType: "active",
  }, { cancelRefetch });
}

/** Snapshot retained reads when an idle socket starts; later first loads are not stale. */
export function retainedAuthoredQueryHashes(
  queryClient: Pick<QueryClient, "getQueryCache">,
  modelLabels: readonly string[],
): Set<string> {
  return new Set(queryClient.getQueryCache().findAll({
    predicate: (query) => authoredQueryWithData(query, modelLabels),
  }).map((query) => query.queryHash));
}

function authoredQueryWithData(query: Query, modelLabels: readonly string[]): boolean {
  return query.state.data !== undefined && authoredQueryReadsAnyModel(query.meta, modelLabels);
}

/** One live change: an exact row with its related rows, or a whole model when ``id`` is absent. */
export interface LiveChange {
  model: string;
  id?: string;
  relatedRecords?: readonly { model: string; id: string }[];
}

/** Quiet period that closes a burst of live changes. */
const LIVE_WINDOW_MS = 300;
/** Longest a continuous stream may defer its first buffered change. */
const LIVE_MAX_WAIT_MS = 2000;

/** Refine's resource-wide invalidation scope and one mounted subscription's delivery. */
export interface LiveDelivery<Change extends LiveChange> {
  queryKey: QueryKey;
  callback: (changes: readonly Change[]) => void;
}

export interface LiveInvalidation<Change extends LiveChange> {
  push: (change: Change, deliveries?: readonly LiveDelivery<Change>[]) => void;
  remove: (delivery: LiveDelivery<Change>) => void;
  clear: () => void;
}

/**
 * Coalesce live changes so each affected read refetches once per burst.
 *
 * A burst closes after a quiet window or the max wait, and one flush refetches
 * the union of reads it affects. A read still fetching at the flush is never
 * cancelled: it commits its snapshot, which is no older than the data already on
 * screen, and refetches as soon as it settles, so every change is still followed
 * by a read that started after it. Cancelling instead would starve any read
 * slower than the gaps of a continuous stream (a history backfill), leaving it
 * on its old data or its loading state for as long as the stream lasts.
 */
export function createLiveInvalidation<Change extends LiveChange = LiveChange>(
  queryClient?: Pick<QueryClient, "invalidateQueries" | "getQueryCache">,
): LiveInvalidation<Change> {
  let changes: Change[] = [];
  const deliveries = new Map<LiveDelivery<Change>, Change[]>();
  const awaited = new Set<string>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let firstAt: number | undefined;
  let unsubscribe: (() => void) | undefined;

  function refetch(predicate: (query: Query) => boolean): void {
    void queryClient?.invalidateQueries({ predicate, type: "all", refetchType: "active" }, { cancelRefetch: false });
  }

  function stopUnusedListener(): void {
    if (awaited.size === 0) {
      unsubscribe?.();
      unsubscribe = undefined;
    }
  }

  function flush(): void {
    const batch = changes;
    const notifications = [...deliveries];
    changes = [];
    deliveries.clear();
    timer = undefined;
    firstAt = undefined;
    const reads = (query: Query) => batch.some((change) => authoredQueryReadsLiveChange(query.meta, change));
    // Snapshot running reads before any callback starts a fresh read. Refine
    // invalidates resourceAll; its public key builder supplies that exact scope.
    const cache = queryClient?.getQueryCache();
    const affected = [
      ...(cache?.findAll({ predicate: reads }) ?? []),
      ...notifications.flatMap(([delivery]) => cache?.findAll({ queryKey: delivery.queryKey, type: "active" }) ?? []),
    ];
    for (const query of affected) {
      if (query.state.fetchStatus === "fetching") awaited.add(query.queryHash);
    }
    if (awaited.size > 0 && !unsubscribe) {
      // Both authored and resource reads owe one fresh snapshot after settling.
      unsubscribe = cache?.subscribe((event) => {
        const hash = event.query.queryHash;
        if (!awaited.has(hash)) return;
        if (event.type === "removed") awaited.delete(hash);
        else if (event.type === "updated" && event.query.state.fetchStatus !== "fetching") {
          awaited.delete(hash);
          refetch((query) => query.queryHash === hash);
        }
        stopUnusedListener();
      });
    }
    refetch((query) => !awaited.has(query.queryHash) && reads(query));
    for (const [delivery, events] of notifications) delivery.callback(events);
  }

  return {
    remove(delivery) {
      deliveries.delete(delivery);
    },
    clear() {
      if (timer !== undefined) clearTimeout(timer);
      changes = [];
      deliveries.clear();
      awaited.clear();
      timer = undefined;
      firstAt = undefined;
      stopUnusedListener();
    },
    push(change, consumers = []) {
      changes.push(change);
      for (const consumer of consumers) {
        const pending = deliveries.get(consumer) ?? [];
        pending.push(change);
        deliveries.set(consumer, pending);
      }
      const now = performance.now();
      firstAt ??= now;
      if (timer !== undefined) clearTimeout(timer);
      timer = setTimeout(flush, Math.max(0, Math.min(LIVE_WINDOW_MS, firstAt + LIVE_MAX_WAIT_MS - now)));
    },
  };
}

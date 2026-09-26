import type { Query, QueryClient } from "@tanstack/react-query";

import { recordValue } from "./dialect/wire";

export function authoredQueryMeta(
  modelLabels: readonly string[],
): Record<string, unknown> | undefined {
  return modelLabels.length > 0 ? { angeeModels: [...modelLabels] } : undefined;
}

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
    || (change.relatedRecords ?? []).some((record) => authoredQueryReadsChange(meta, record.model, record.id));
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
  relatedRecords?: readonly { model: string; id: string }[];
}

/** Quiet period that closes a burst of live changes. */
const LIVE_WINDOW_MS = 300;
/** Longest a continuous stream may defer its first buffered change. */
const LIVE_MAX_WAIT_MS = 2000;

export interface AuthoredLiveInvalidation {
  push: (change: AuthoredLiveChange) => void;
}

/**
 * Coalesce live changes so each affected read refetches once per burst.
 *
 * A change cancels matching in-flight requests as it arrives, so a response
 * read before the change never commits. Only the refetch is deferred: one flush
 * applies the shared protocol to the union of the burst, so affected reads keep
 * their last committed data for at most the max wait. Under a continuous stream
 * a read slower than the max wait is restarted at each flush.
 */
export function createAuthoredLiveInvalidation(
  queryClient: Pick<QueryClient, "cancelQueries" | "invalidateQueries">,
): AuthoredLiveInvalidation {
  let changes: AuthoredLiveChange[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;
  let firstAt: number | undefined;

  function flush(): void {
    const batch = changes;
    changes = [];
    timer = undefined;
    firstAt = undefined;
    void invalidateAuthoredQueriesMatching(
      queryClient,
      (query) => batch.some((change) => authoredQueryReadsLiveChange(query.meta, change)),
    );
  }

  return {
    push(change) {
      void queryClient.cancelQueries({
        predicate: (query) => query.state.fetchStatus !== "idle"
          && authoredQueryReadsLiveChange(query.meta, change),
      });
      changes.push(change);
      const now = performance.now();
      firstAt ??= now;
      if (timer !== undefined) clearTimeout(timer);
      timer = setTimeout(flush, Math.max(0, Math.min(LIVE_WINDOW_MS, firstAt + LIVE_MAX_WAIT_MS - now)));
    },
  };
}

/** One cancellation/refetch protocol shared by model-wide, exact-row and live invalidation. */
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

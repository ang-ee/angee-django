import { useMemo } from "react";
import type { UseQueryResult } from "@tanstack/react-query";

/**
 * What a batch's callers read of one native query result. Query notifies a batch
 * only when one of these changes, so a read going stale or a retry's count does not
 * render its owner.
 */
export type QueryBatchEntry<TData, TError = Error> = Readonly<
  Pick<UseQueryResult<TData, TError>, "data" | "dataUpdatedAt" | "error" | "isFetching" | "refetch">
>;

/**
 * The one `useQueries` `combine` of every batch read. A caller passes it as a fresh
 * function each render, so Query re-runs it on that render's results (an option such
 * as `enabled`, or a rebuilt cache entry, changes them without a new hash), and its
 * structural sharing keeps the array and each entry that did not change.
 */
export function projectQueryBatch<TData, TError>(
  results: readonly UseQueryResult<TData, TError>[],
): QueryBatchEntry<TData, TError>[] {
  return results.map(({ data, dataUpdatedAt, error, isFetching, refetch }) => ({
    data, dataUpdatedAt, error, isFetching, refetch,
  }));
}

/**
 * Each label's result, by its slot among the batch's results, which Query keeps in
 * the order of the requests. A label without one is a broken batch: it fails here,
 * naming `hook`, rather than hand a caller another request's result or nothing.
 */
export function labelBatchResults<TResult>(
  hook: string,
  slots: readonly (readonly [string, number])[],
  results: readonly TResult[],
): ReadonlyMap<string, TResult> {
  return new Map(slots.map(([key, slot]) => {
    const result = results[slot];
    if (result === undefined) {
      throw new Error(`${hook}: "${key}" has no result among the batch's ${results.length}.`);
    }
    return [key, result] as const;
  }));
}

/**
 * Derive each entry of a batch, reusing an output while its entry is the identical
 * object and `inputs` are unchanged, so a change derives again only the entries it
 * touched. `derive` reads only its entry, its key and what `inputs` names.
 */
export function useDerivedBatch<TEntry extends object, TOutput extends object>(
  batch: ReadonlyMap<string, TEntry>,
  derive: (entry: TEntry, key: string) => TOutput,
  inputs: readonly unknown[],
): ReadonlyMap<string, TOutput> {
  // One table per set of inputs, by entry and then label: labels sharing a request
  // may derive differently (two facets of one read).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const derived = useMemo(() => new WeakMap<TEntry, Map<string, TOutput>>(), inputs);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => new Map([...batch].map(([key, entry]) => {
    let outputs = derived.get(entry);
    if (!outputs) derived.set(entry, outputs = new Map());
    let output = outputs.get(key);
    if (!output) outputs.set(key, output = derive(entry, key));
    return [key, output] as const;
  })), [batch, derived]);
}

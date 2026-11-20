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

/** A batch's entries by their request's native query hash. */
export type QueryBatchEntries<TData, TError = Error> = Readonly<Record<string, QueryBatchEntry<TData, TError>>>;

/**
 * The one `useQueries` `combine` of every batch read: each result's entry under its
 * request's hash (`hashes`, in the order of the requests). A caller passes it as a
 * fresh function each render, so Query re-runs it on that render's results (an
 * option such as `enabled`, or a rebuilt cache entry, changes them without a new
 * hash), and its structural sharing matches a plain object's entries by key: an entry
 * that did not change keeps its identity wherever its request moved in the batch.
 */
export function projectQueryBatch<TData, TError>(
  hashes: readonly string[],
  results: readonly UseQueryResult<TData, TError>[],
): QueryBatchEntries<TData, TError> {
  return Object.fromEntries(results.map(({ data, dataUpdatedAt, error, isFetching, refetch }, index) => [
    hashes[index], { data, dataUpdatedAt, error, isFetching, refetch },
  ]));
}

/**
 * Each label's entry, by its request's hash. A label without one is a broken batch:
 * it fails here, naming `hook`, rather than hand a caller another request's entry or
 * nothing.
 */
export function labelBatchResults<TEntry>(
  hook: string,
  labels: readonly (readonly [label: string, hash: string])[],
  entries: Readonly<Record<string, TEntry>>,
): ReadonlyMap<string, TEntry> {
  return new Map(labels.map(([label, hash]) => {
    if (!Object.hasOwn(entries, hash)) {
      throw new Error(`${hook}: "${label}" has no result for its request.`);
    }
    return [label, entries[hash]!] as const;
  }));
}

/**
 * Derive each entry of a batch, reusing an output while its entry is the identical
 * object and `inputs` are unchanged, so a change derives again only the entries it
 * touched. `derive` reads only its entry, its key and what `inputs` names. This is
 * every batch's one derivation, a source's `select` included: a batch serves one
 * request to several labels that may derive differently (two facets of one read),
 * which Query's per-query `select` cannot express.
 */
export function useDerivedBatch<TEntry extends object, TOutput extends object>(
  batch: ReadonlyMap<string, TEntry>,
  derive: (entry: TEntry, key: string) => TOutput,
  inputs: readonly unknown[],
): ReadonlyMap<string, TOutput> {
  // One table per set of inputs, by entry and then label.
  const derived = useMemo(() => new WeakMap<TEntry, Map<string, TOutput>>(), inputs);
  return useMemo(() => new Map([...batch].map(([key, entry]) => {
    let outputs = derived.get(entry);
    if (!outputs) derived.set(entry, outputs = new Map());
    let output = outputs.get(key);
    if (!output) outputs.set(key, output = derive(entry, key));
    return [key, output] as const;
  })), [batch, derived]);
}

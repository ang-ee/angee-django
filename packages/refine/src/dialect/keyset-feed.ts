import {
  hashKey,
  infiniteQueryOptions,
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
  type QueryFunctionContext,
  type QueryKey,
  type UseInfiniteQueryOptions,
  type UseInfiniteQueryResult,
} from "@tanstack/react-query";
import { useCallback } from "react";
import { useAcceptQueryData, type QueryDataAcceptance, type AuthoredLiveChange } from "../query-invalidation";
import { useDataProvider } from "@refinedev/core";
import type { DocumentData } from "../typed-document";
import type { AuthoredDocument, AuthoredVariables } from "./authored-hooks";
import { useAuthoredLiveInterest } from "./authored-hooks";
import { useActiveDataProviderName } from "./data-provider-context";
import { authoredQueryKey, requestAuthoredData, sharedAuthoredMeta, useAuthoredErrorPolicy } from "./authored-query-options";

/** Public identities stay opaque; the domain supplies presentation order. */
export interface KeysetRow { id: string }

/** A server-owned fixed window; an empty window can still have older history. */
export interface KeysetFeedWindow<TRow extends KeysetRow, TMetadata = undefined> {
  rows: readonly TRow[];
  count: number;
  older_cursor: string | null;
  has_older: boolean;
  has_more_in_window: boolean;
  has_older_than_through: boolean;
  /** Domain-owned snapshot facts remain alongside every page, including empty pages. */
  metadata?: TMetadata;
  newer_cursor?: string | null;
  has_newer?: boolean;
  has_newer_than_before?: boolean;
}

/** Native infinite-query page parameters distinguish older and newer discovery. */
export type KeysetFeedCursor = string | null | { after: string } | { start: true };

/** Every requested ID appears exactly once as a survivor or an absence. */
export interface KeysetFeedRevalidation<TRow extends KeysetRow> {
  rows: readonly TRow[];
  absent_ids: readonly string[];
}

/** Native pages retain fixed cuts even when every row in a window disappears. */
export interface KeysetFeedPage<TRow extends KeysetRow, TMetadata = undefined> {
  rows: readonly TRow[];
  through: string | null;
  hasOlder: boolean;
  count: number;
  metadata?: TMetadata;
  before?: string | null;
  newer?: string | null;
  hasNewer?: boolean;
}

interface KeysetFeedReads<TRow extends KeysetRow, TMetadata = undefined> {
  queryKey: QueryKey;
  pageSize: number;
  /** Full current rows in the same authorized scope and projection as revalidate. */
  window: (
    before: string | null,
    through: string | null,
    limit: number,
    context: QueryFunctionContext,
  ) => Promise<KeysetFeedWindow<TRow, TMetadata>>;
  /** Exact row models whose retained updates can use bounded revalidation. */
  revalidateModels?: readonly string[];
  revalidate?: (
    ids: string[],
    context: QueryFunctionContext,
  ) => Promise<KeysetFeedRevalidation<TRow>>;
  newer?: (after: string, limit: number, context: QueryFunctionContext) => Promise<KeysetFeedWindow<TRow, TMetadata>>;
}

/**
 * Loaded history lives only in native InfiniteData. Refetch walks each original
 * fixed window and revalidates its owned IDs not observed in those reads when revalidation is
 * supplied. Without it, native sequential reads replace pages on refetch.
 * A moved row in a revalidated feed keeps its native-page owner; the derived
 * presentation applies the server's order.
 *
 * Every HTTP request is bounded, but a complete refresh grows with loaded history
 * and new head arrivals. The adapter owns any cross-request server snapshot.
 */
export function keysetFeedOptions<TRow extends KeysetRow, TMetadata = undefined>(
  client: QueryClient,
  reads: KeysetFeedReads<TRow, TMetadata>,
): ReturnType<typeof infiniteQueryOptions<
  KeysetFeedPage<TRow, TMetadata>, Error, InfiniteData<KeysetFeedPage<TRow, TMetadata>, KeysetFeedCursor>,
  QueryKey, KeysetFeedCursor
>> {
  type Page = KeysetFeedPage<TRow, TMetadata>;
  const cached = () => client.getQueryData<InfiniteData<Page, KeysetFeedCursor>>(reads.queryKey);
  // One in-flight native infinite fetch shares a bounded revalidation request
  // across its pages. Clear on completion/abort; no rows survive outside Query.
  let targetedRead: { signal: AbortSignal; result: Promise<KeysetFeedRevalidation<TRow>>; stop: () => void } | undefined;
  const clearTargetedRead = () => {
    targetedRead?.stop();
    targetedRead = undefined;
  };
  if (!Number.isInteger(reads.pageSize) || reads.pageSize < 1 || reads.pageSize > 200) {
    throw new Error("Keyset feed page size must be between 1 and 200.");
  }
  return infiniteQueryOptions<Page, Error, InfiniteData<Page, KeysetFeedCursor>, QueryKey, KeysetFeedCursor>({
    queryKey: reads.queryKey,
    initialPageParam: { start: true },
    placeholderData: undefined,
    async queryFn(context): Promise<Page> {
      try {
        const previous = reads.revalidate ? cached() : undefined;
        const index = previous?.pageParams.findIndex(param => hashKey([param ?? { start: true }]) === hashKey([context.pageParam ?? { start: true }])) ?? -1;
        const oldPage = index < 0 ? undefined : previous?.pages[index];
        const owned = new Set(previous?.pages.flatMap((page) => page.rows.map((row) => row.id)));
        const changes = context.meta?.angeeChanges as readonly AuthoredLiveChange[] | undefined;
        const targeted = reads.revalidate && oldPage && previous && changes?.length
          && previous.pages.every((page) => page.metadata === undefined)
          && changes.every((change) => change.action === "update" && change.id
            && reads.revalidateModels?.includes(change.model) && owned.has(change.id));
        if (targeted && reads.revalidate && previous && oldPage && changes) {
          const signal = context.signal;
          if (!targetedRead || targetedRead.signal !== signal) {
            clearTargetedRead();
            const ids = [...new Set(changes.map((change) => change.id!))];
            const result = (async () => {
              const rows: TRow[] = [];
              const absent_ids: string[] = [];
              for (let offset = 0; offset < ids.length; offset += reads.pageSize) {
                const requested = ids.slice(offset, offset + reads.pageSize);
                const fresh = await reads.revalidate!(requested, context);
                signal.throwIfAborted();
                assertRevalidationPartition(requested, fresh);
                rows.push(...fresh.rows);
                absent_ids.push(...fresh.absent_ids);
              }
              return { rows, absent_ids };
            })();
            const abort = () => clearTargetedRead();
            signal.addEventListener("abort", abort, { once: true });
            targetedRead = { signal, result, stop: () => signal.removeEventListener("abort", abort) };
          }
          let fresh: KeysetFeedRevalidation<TRow>;
          try {
            fresh = await targetedRead.result;
          } catch (error) {
            clearTargetedRead();
            throw error;
          }
          if (index === previous.pages.length - 1) clearTargetedRead();
          // Absence changes membership/counts/cursors. Every page falls back to
          // its normal fixed-window refresh; silence alone never removes a row.
          if (fresh.absent_ids.length === 0) {
            const replacements = new Map(fresh.rows.map((row) => [row.id, row]));
            return { ...oldPage, rows: oldPage.rows.map((row) => replacements.get(row.id) ?? row) };
          }
        }
        const newer = context.pageParam !== null && typeof context.pageParam === "object" && "after" in context.pageParam
          ? context.pageParam : null;
        const older = typeof context.pageParam === "string" ? context.pageParam : null;
        if (!reads.revalidate || !oldPage || oldPage.through === null) {
          if (newer && !reads.newer) throw new Error("Keyset feed does not support newer-page reads.");
          const page = newer && reads.newer
            ? await reads.newer(newer.after, reads.pageSize, context)
            : await reads.window(older, null, reads.pageSize, context);
          context.signal.throwIfAborted();
          return {
            rows: reads.revalidate ? page.rows.filter((row) => !owned.has(row.id)
              || oldPage?.rows.some((retained) => retained.id === row.id)) : page.rows,
            through: page.older_cursor,
            hasOlder: page.has_older,
            count: page.count,
            metadata: page.metadata,
            before: newer ? page.newer_cursor : undefined,
            newer: page.newer_cursor,
            hasNewer: page.has_newer,
          };
        }

        // These indexes contain IDs, live only for this request, and retain no rows.
        if (!previous) throw new Error("Keyset feed lost its retained page context.");
        const earlierIds = new Set(previous.pages.slice(0, index).flatMap((page) => page.rows.map((row) => row.id)));
        const mine = [...new Set(oldPage.rows.map((row) => row.id).filter((id) => !earlierIds.has(id)))];
        const observed = new Map<string, TRow>();
        const visited = new Set<string | null>();
        let before = newer ? oldPage.before ?? null : older;
        let page: KeysetFeedWindow<TRow, TMetadata>;
        let firstWindow: KeysetFeedWindow<TRow, TMetadata> | undefined;
        do {
          visited.add(before);
          page = await reads.window(before, oldPage.through, reads.pageSize, context);
          firstWindow ??= page;
          context.signal.throwIfAborted();
          for (const row of page.rows) {
            // The later full observation wins within this producing page.
            observed.set(row.id, row);
          }
          before = page.older_cursor;
          if (page.has_more_in_window && (before === null || visited.has(before))) {
            throw new Error("Keyset feed returned no advancing window cursor.");
          }
        } while (page.has_more_in_window);

        // Window omission is not absence: moved or hidden owned rows still
        // require the adapter's exact permission-filtered partition.
        const survivors = mine.flatMap((id) => {
          const row = observed.get(id);
          return row ? [row] : [];
        });
        const unobserved = mine.filter((id) => !observed.has(id));
        for (let offset = 0; offset < unobserved.length; offset += reads.pageSize) {
          const ids = unobserved.slice(offset, offset + reads.pageSize);
          const result = await reads.revalidate(ids, context);
          context.signal.throwIfAborted();
          assertRevalidationPartition(ids, result);
          survivors.push(...result.rows);
        }
        return {
          rows: [...survivors, ...[...observed.values()].filter((row) => !owned.has(row.id))],
          through: oldPage.through,
          hasOlder: page.has_older_than_through,
          count: page.count,
          metadata: page.metadata,
          before: oldPage.before,
          newer: newer ? oldPage.newer : firstWindow.newer_cursor,
          hasNewer: newer ? firstWindow.has_newer_than_before : firstWindow.has_newer,
        };
      } catch (error) {
        clearTargetedRead();
        throw error;
      }
    },
    getNextPageParam(lastPage, allPages) {
      // Native sequential refetch must not move older cuts with a growing head.
      const savedNext = reads.revalidate ? cached()?.pageParams[allPages.length] : undefined;
      // Null is the original anchored page after newer pages were prepended;
      // TanStack reserves null for exhaustion, so keep that saved boundary as
      // an explicit first-window cursor during sequential refetch.
      if (savedNext === null) return { start: true };
      if (savedNext !== undefined) return savedNext;
      return lastPage.hasOlder ? lastPage.through ?? undefined : undefined;
    },
    getPreviousPageParam(firstPage) {
      return reads.newer && firstPage.hasNewer && firstPage.newer ? { after: firstPage.newer } : undefined;
    },
  });
}

function assertRevalidationPartition<TRow extends KeysetRow>(
  ids: readonly string[], result: KeysetFeedRevalidation<TRow>,
): void {
  const represented = [...result.rows.map((row) => row.id), ...result.absent_ids];
  const checked = new Set(represented);
  if (represented.length !== ids.length || checked.size !== ids.length || ids.some((id) => !checked.has(id))) {
    throw new Error("Keyset feed revalidation did not partition every requested ID.");
  }
}

/** A domain may prove new head membership or explicit authorized absence. */
export interface KeysetFeedAcceptance {
  insert?: boolean;
  absent_ids?: readonly string[];
}

/** Derive display order without retaining a second copy outside Query's pages. */
export function keysetFeedRows<TRow extends KeysetRow, TMetadata = unknown>(
  data: InfiniteData<KeysetFeedPage<TRow, TMetadata>, KeysetFeedCursor> | undefined,
  compare: (left: TRow, right: TRow) => number,
): TRow[] {
  const seen = new Set<string>();
  return (data?.pages.flatMap((page) => page.rows) ?? [])
    // An unseen row can move between sequential window reads. Prefer its
    // later observation before applying order; the next refresh fixes ownership.
    .reverse()
    .filter((row) => {
      if (seen.has(row.id)) return false;
      seen.add(row.id);
      return true;
    })
    .sort(compare);
}

/** Typed document adapters keep domain projections and variables above Refine. */
export interface AuthoredKeysetFeedOptions<
  TRow extends KeysetRow,
  TWindow extends AuthoredDocument,
  TRevalidation extends AuthoredDocument,
  TMetadata = undefined,
> {
  /** Supplied by the session owner; Refine never imports App or domain auth. */
  actor: string | undefined;
  enabled?: boolean;
  dataProviderName?: string;
  models: readonly string[];
  /** Exact record interests use the authored-query metadata owner. */
  records?: readonly { model: string; id: string }[];
  pageSize: number;
  /** Native request lifecycle policy; identity and retained pages stay with this owner. */
  queryOptions?: Pick<UseInfiniteQueryOptions<
    KeysetFeedPage<TRow, TMetadata>, Error, InfiniteData<KeysetFeedPage<TRow, TMetadata>, KeysetFeedCursor>,
    QueryKey, KeysetFeedCursor
  >, "staleTime" | "gcTime" | "retry" | "retryDelay" | "retryOnMount"
    | "refetchOnMount" | "refetchOnWindowFocus" | "refetchOnReconnect">;
  window: {
    document: TWindow;
    variables: (before: string | null, through: string | null, limit: number) => AuthoredVariables<TWindow>;
    select: (data: DocumentData<TWindow>) => KeysetFeedWindow<TRow, TMetadata>;
    /** Optional native newer-page read using the same operation and scope. */
    newerVariables?: (after: string, limit: number) => AuthoredVariables<TWindow>;
  };
  /** Omit for snapshot feeds whose explicit refresh restarts native page discovery. */
  revalidate?: {
    /** Canonical row models; unknown models, membership and aggregates refresh windows. */
    models?: readonly string[];
    document: TRevalidation;
    variables: (ids: string[]) => AuthoredVariables<TRevalidation>;
    select: (data: DocumentData<TRevalidation>) => KeysetFeedRevalidation<TRow>;
  };
}

/** Compose authored transport, actor-isolated cache identity and native Query. */
export function useAuthoredKeysetFeed<
  TRow extends KeysetRow,
  TWindow extends AuthoredDocument,
  TRevalidation extends AuthoredDocument,
  TMetadata = undefined,
>(options: AuthoredKeysetFeedOptions<TRow, TWindow, TRevalidation, TMetadata>): {
  /** Native tracked result; read only the properties the consumer needs. */
  query: UseInfiniteQueryResult<InfiniteData<KeysetFeedPage<TRow, TMetadata>, KeysetFeedCursor>, Error>;
  /** Discard loaded history and explicitly fetch the first page, including disabled/static feeds. */
  restart: () => Promise<void>;
  /** Adopt full returned rows in native pages; never manufacture pagination facts. */
  acceptResponse: (rows: readonly TRow[], options?: KeysetFeedAcceptance) => Promise<QueryDataAcceptance | undefined>;
} {
  const client = useQueryClient();
  const accept = useAcceptQueryData();
  const dataProvider = useDataProvider();
  const activeProvider = useActiveDataProviderName();
  const provider = options.dataProviderName ?? activeProvider ?? "default";
  const { window, revalidate, actor, pageSize, models } = options;
  const enabled = Boolean(actor) && (options.enabled ?? true);
  const queryKey = ["angee", "authored", "keyset-feed", actor,
    authoredQueryKey(window.document, window.variables(null, null, pageSize), provider),
    ...(revalidate ? [authoredQueryKey(revalidate.document, revalidate.variables([]), provider)] : []),
  ] as const;
  const configured = keysetFeedOptions(client, {
    queryKey,
    pageSize,
    window: async (before, through, limit, context) => window.select(
      await requestAuthoredData<DocumentData<TWindow>>(
        dataProvider, provider, window.document, window.variables(before, through, limit), context,
      ),
    ),
    newer: window.newerVariables ? async (after, limit, context) => window.select(
      await requestAuthoredData<DocumentData<TWindow>>(
        dataProvider, provider, window.document, window.newerVariables!(after, limit), context,
      ),
    ) : undefined,
    revalidateModels: revalidate?.models,
    revalidate: revalidate ? async (ids, context) => revalidate.select(
      await requestAuthoredData<DocumentData<TRevalidation>>(
        dataProvider, provider, revalidate.document, revalidate.variables(ids), context,
      ),
    ) : undefined,
  });
  useAuthoredLiveInterest(enabled, models);
  useAuthoredErrorPolicy([queryKey]);
  const result = useInfiniteQuery({
    ...options.queryOptions, ...configured, enabled,
    meta: sharedAuthoredMeta(client, queryKey, models, options.records, [], options.queryOptions),
  });
  const { refetch } = result;
  // Track queryKey through hashKey intentionally: TanStack treats equal hashes as the same query.
  const queryHash = hashKey(queryKey);
  const restart = useCallback(async () => {
    if (!actor) return;
    await client.resetQueries({ queryKey, exact: true }, { throwOnError: true });
    // Native reset refetches active queries, but skips disabled/static observers.
    if (client.getQueryData(queryKey) === undefined) await refetch({ throwOnError: true });
  }, [actor, client, queryHash, refetch]);
  const acceptResponse = useCallback(async (rows: readonly TRow[], acceptance: KeysetFeedAcceptance = {}) => {
    if (!actor) throw new Error("Keyset feed acceptance requires an actor.");
    return accept<InfiniteData<KeysetFeedPage<TRow, TMetadata>, KeysetFeedCursor>>(
      queryKey, (current) => {
        if (!current?.pages.length) throw new Error("Load the keyset head before accepting rows.");
        const replacements = new Map(rows.map((row) => [row.id, row]));
        const absent = new Set(acceptance.absent_ids ?? []);
        if (rows.some((row) => absent.has(row.id))) throw new Error("Accepted feed rows cannot also be absent.");
        const retained = new Set(current.pages.flatMap((page) => page.rows.map((row) => row.id)));
        const added = acceptance.insert ? rows.filter((row) => !retained.has(row.id)) : [];
        return {
          ...current,
          pages: current.pages.map((page, index) => ({
            ...page,
            rows: [
              ...(index === 0 ? added : []),
              ...page.rows.filter((row) => !absent.has(row.id)).map((row) => replacements.get(row.id) ?? row),
            ],
          })),
        };
      },
      // Counts, cursor flags and projection metadata still need the server.
      { complete: false },
    );
  }, [actor, accept, queryHash]);
  return { query: result, restart, acceptResponse };
}

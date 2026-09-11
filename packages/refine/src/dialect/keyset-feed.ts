import {
  infiniteQueryOptions,
  useInfiniteQuery,
  useQueryClient,
  type InfiniteData,
  type QueryClient,
  type QueryFunctionContext,
  type QueryKey,
  type UseInfiniteQueryResult,
} from "@tanstack/react-query";
import { useDataProvider } from "@refinedev/core";
import type { DocumentData } from "../typed-document";
import type { AuthoredDocument, AuthoredVariables } from "./authored-hooks";
import { useAuthoredLiveInterest } from "./authored-hooks";
import { useActiveDataProviderName } from "./data-provider-context";
import { authoredQueryKey, requestAuthoredData, sharedAuthoredMeta, useAuthoredErrorPolicy } from "./authored-query-options";

/** Public identities stay opaque; the domain supplies presentation order. */
export interface KeysetRow { id: string }

/** A server-owned fixed window; an empty window can still have older history. */
export interface KeysetFeedWindow<TRow extends KeysetRow> {
  rows: readonly TRow[];
  count: number;
  older_cursor: string | null;
  has_older: boolean;
  has_more_in_window: boolean;
  has_older_than_through: boolean;
}

/** Every requested ID appears exactly once as a survivor or an absence. */
export interface KeysetFeedRevalidation<TRow extends KeysetRow> {
  rows: readonly TRow[];
  absent_ids: readonly string[];
}

/** Native pages retain fixed cuts even when every row in a window disappears. */
export interface KeysetFeedPage<TRow extends KeysetRow> {
  rows: readonly TRow[];
  through: string | null;
  hasOlder: boolean;
  count: number;
}

interface KeysetFeedReads<TRow extends KeysetRow> {
  queryKey: QueryKey;
  pageSize: number;
  window: (
    before: string | null,
    through: string | null,
    limit: number,
    context: QueryFunctionContext,
  ) => Promise<KeysetFeedWindow<TRow>>;
  revalidate: (
    ids: string[],
    context: QueryFunctionContext,
  ) => Promise<KeysetFeedRevalidation<TRow>>;
}

/**
 * Loaded history lives only in native InfiniteData. Refetch walks each original
 * fixed window and revalidates all IDs owned by that page. A moved row keeps
 * its native-page owner; the derived presentation applies the server's order.
 *
 * Every HTTP request is bounded, but a complete refresh grows with loaded history
 * and new head arrivals. There is no cross-request server snapshot or maxPages.
 */
export function keysetFeedOptions<TRow extends KeysetRow>(
  client: QueryClient,
  reads: KeysetFeedReads<TRow>,
): ReturnType<typeof infiniteQueryOptions<
  KeysetFeedPage<TRow>, Error, InfiniteData<KeysetFeedPage<TRow>, string | null>,
  QueryKey, string | null
>> {
  type Page = KeysetFeedPage<TRow>;
  const cached = () => client.getQueryData<InfiniteData<Page, string | null>>(reads.queryKey);
  if (!Number.isInteger(reads.pageSize) || reads.pageSize < 1 || reads.pageSize > 200) {
    throw new Error("Keyset feed page size must be between 1 and 200.");
  }
  return infiniteQueryOptions<Page, Error, InfiniteData<Page, string | null>, QueryKey, string | null>({
    queryKey: reads.queryKey,
    initialPageParam: null,
    placeholderData: undefined,
    async queryFn(context): Promise<Page> {
      const previous = cached();
      const index = previous?.pageParams.indexOf(context.pageParam) ?? -1;
      const oldPage = index < 0 ? undefined : previous?.pages[index];
      const owned = new Set(previous?.pages.flatMap((page) => page.rows.map((row) => row.id)));
      if (!oldPage || oldPage.through === null) {
        const page = await reads.window(context.pageParam, null, reads.pageSize, context);
        context.signal.throwIfAborted();
        return {
          rows: page.rows.filter((row) => !owned.has(row.id)),
          through: page.older_cursor,
          hasOlder: page.has_older,
          count: page.count,
        };
      }

      // These indexes contain IDs, live only for this request, and retain no rows.
      const earlierIds = new Set(previous!.pages.slice(0, index).flatMap((page) => page.rows.map((row) => row.id)));
      const mine = [...new Set(oldPage.rows.map((row) => row.id).filter((id) => !earlierIds.has(id)))];
      const fresh: TRow[] = [];
      const freshIds = new Set<string>();
      const visited = new Set<string | null>();
      let before = context.pageParam;
      let page: KeysetFeedWindow<TRow>;
      do {
        visited.add(before);
        page = await reads.window(before, oldPage.through, reads.pageSize, context);
        context.signal.throwIfAborted();
        for (const row of page.rows) {
          if (!owned.has(row.id) && !freshIds.has(row.id)) {
            freshIds.add(row.id);
            fresh.push(row);
          }
        }
        before = page.older_cursor;
        if (page.has_more_in_window && (before === null || visited.has(before))) {
          throw new Error("Keyset feed returned no advancing window cursor.");
        }
      } while (page.has_more_in_window);

      const survivors: TRow[] = [];
      for (let offset = 0; offset < mine.length; offset += reads.pageSize) {
        const ids = mine.slice(offset, offset + reads.pageSize);
        const result = await reads.revalidate(ids, context);
        context.signal.throwIfAborted();
        const represented = [...result.rows.map((row) => row.id), ...result.absent_ids];
        const checked = new Set(represented);
        if (represented.length !== ids.length || checked.size !== ids.length || ids.some((id) => !checked.has(id))) {
          throw new Error("Keyset feed revalidation did not partition every requested ID.");
        }
        survivors.push(...result.rows);
      }
      return {
        rows: [...survivors, ...fresh],
        through: oldPage.through,
        hasOlder: page.has_older_than_through,
        count: page.count,
      };
    },
    getNextPageParam(lastPage, allPages) {
      // Native sequential refetch must not move older cuts with a growing head.
      const savedNext = cached()?.pageParams[allPages.length];
      return savedNext !== undefined ? savedNext : lastPage.hasOlder ? lastPage.through ?? undefined : undefined;
    },
  });
}

/** Derive display order without retaining a second copy outside Query's pages. */
export function keysetFeedRows<TRow extends KeysetRow>(
  data: InfiniteData<KeysetFeedPage<TRow>, string | null> | undefined,
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
> {
  /** Supplied by the session owner; Refine never imports App or domain auth. */
  actor: string | undefined;
  enabled?: boolean;
  dataProviderName?: string;
  models: readonly string[];
  pageSize: number;
  window: {
    document: TWindow;
    variables: (before: string | null, through: string | null, limit: number) => AuthoredVariables<TWindow>;
    select: (data: DocumentData<TWindow>) => KeysetFeedWindow<TRow>;
  };
  revalidate: {
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
>(options: AuthoredKeysetFeedOptions<TRow, TWindow, TRevalidation>): UseInfiniteQueryResult<
  InfiniteData<KeysetFeedPage<TRow>, string | null>, Error
> {
  const client = useQueryClient();
  const dataProvider = useDataProvider();
  const activeProvider = useActiveDataProviderName();
  const provider = options.dataProviderName ?? activeProvider ?? "default";
  const { window, revalidate, actor, pageSize, models } = options;
  const enabled = Boolean(actor) && (options.enabled ?? true);
  const queryKey = ["angee", "authored", "keyset-feed", actor,
    authoredQueryKey(window.document, window.variables(null, null, pageSize), provider),
    authoredQueryKey(revalidate.document, revalidate.variables([]), provider),
  ] as const;
  const configured = keysetFeedOptions(client, {
    queryKey,
    pageSize,
    window: async (before, through, limit, context) => window.select(
      await requestAuthoredData<DocumentData<TWindow>>(
        dataProvider, provider, window.document, window.variables(before, through, limit), context,
      ),
    ),
    revalidate: async (ids, context) => revalidate.select(
      await requestAuthoredData<DocumentData<TRevalidation>>(
        dataProvider, provider, revalidate.document, revalidate.variables(ids), context,
      ),
    ),
  });
  useAuthoredLiveInterest(enabled, models);
  useAuthoredErrorPolicy([queryKey]);
  return useInfiniteQuery({ ...configured, enabled, meta: sharedAuthoredMeta(client, queryKey, models) });
}

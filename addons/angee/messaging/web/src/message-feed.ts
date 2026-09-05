import {
  infiniteQueryOptions,
  type InfiniteData,
  type QueryClient,
  type QueryFunctionContext,
  type QueryKey,
} from "@tanstack/react-query";
import type { DocumentData } from "@angee/refine";
import type { ThreadTranscriptDocument, ThreadTranscriptRevalidateDocument, ThreadTranscriptRow } from "./documents";

/** The message owner supplies a total order; cursors and public IDs stay opaque. */
export type OrderedMessage = Pick<ThreadTranscriptRow, "id" | "feed_order_key">;

export type MessageFeedWindow<TMessage extends OrderedMessage> = Omit<
  DocumentData<typeof ThreadTranscriptDocument>["thread_message_feed"], "messages"
> & {
  messages: readonly TMessage[];
};

export type MessageFeedRevalidation<TMessage extends OrderedMessage> = Omit<
  DocumentData<typeof ThreadTranscriptRevalidateDocument>["thread_message_feed_revalidate"], "messages"
> & {
  messages: readonly TMessage[];
};

/** Native pages retain fixed cuts even when every row in a window disappears. */
export interface MessageFeedPage<TMessage extends OrderedMessage> {
  messages: readonly TMessage[];
  through: string | null;
  hasOlder: boolean;
  count: number;
}

interface MessageFeedReads<TMessage extends OrderedMessage> {
  queryKey: QueryKey;
  pageSize: number;
  window: (
    before: string | null,
    through: string | null,
    limit: number,
    context: QueryFunctionContext,
  ) => Promise<MessageFeedWindow<TMessage>>;
  revalidate: (
    ids: string[],
    context: QueryFunctionContext,
  ) => Promise<MessageFeedRevalidation<TMessage>>;
}

/**
 * Message history lives only in native InfiniteData. Refetch walks each original
 * fixed window and revalidates all IDs owned by that page. A moved message keeps
 * its native-page owner; the derived presentation applies the server's order.
 *
 * Every HTTP request is bounded, but a complete refresh grows with loaded history
 * and new head arrivals. There is no cross-request server snapshot or maxPages.
 */
export function messageFeedOptions<TMessage extends OrderedMessage>(
  client: QueryClient,
  reads: MessageFeedReads<TMessage>,
): ReturnType<typeof infiniteQueryOptions<
  MessageFeedPage<TMessage>, Error, InfiniteData<MessageFeedPage<TMessage>, string | null>,
  QueryKey, string | null
>> {
  type Page = MessageFeedPage<TMessage>;
  const cached = () => client.getQueryData<InfiniteData<Page, string | null>>(reads.queryKey);
  if (!Number.isInteger(reads.pageSize) || reads.pageSize < 1 || reads.pageSize > 200) {
    throw new Error("Message feed page size must be between 1 and 200.");
  }
  return infiniteQueryOptions<Page, Error, InfiniteData<Page, string | null>, QueryKey, string | null>({
    queryKey: reads.queryKey,
    initialPageParam: null,
    placeholderData: undefined,
    async queryFn(context): Promise<Page> {
      const previous = cached();
      const index = previous?.pageParams.indexOf(context.pageParam) ?? -1;
      const oldPage = index < 0 ? undefined : previous?.pages[index];
      const owned = new Set(previous?.pages.flatMap((page) => page.messages.map((row) => row.id)));
      if (!oldPage || oldPage.through === null) {
        const page = await reads.window(context.pageParam, null, reads.pageSize, context);
        context.signal.throwIfAborted();
        return {
          messages: page.messages.filter((row) => !owned.has(row.id)),
          through: page.older_cursor,
          hasOlder: page.has_older,
          count: page.count,
        };
      }

      // These indexes contain IDs, live only for this request, and retain no rows.
      const earlierIds = new Set(previous!.pages.slice(0, index).flatMap((page) => page.messages.map((row) => row.id)));
      const mine = [...new Set(oldPage.messages.map((row) => row.id).filter((id) => !earlierIds.has(id)))];
      const fresh: TMessage[] = [];
      const freshIds = new Set<string>();
      const visited = new Set<string | null>();
      let before = context.pageParam;
      let page: MessageFeedWindow<TMessage>;
      do {
        visited.add(before);
        page = await reads.window(before, oldPage.through, reads.pageSize, context);
        context.signal.throwIfAborted();
        for (const row of page.messages) {
          if (!owned.has(row.id) && !freshIds.has(row.id)) {
            freshIds.add(row.id);
            fresh.push(row);
          }
        }
        before = page.older_cursor;
        if (page.has_more_in_window && (before === null || visited.has(before))) {
          throw new Error("Message feed returned no advancing window cursor.");
        }
      } while (page.has_more_in_window);

      const survivors: TMessage[] = [];
      for (let offset = 0; offset < mine.length; offset += reads.pageSize) {
        const ids = mine.slice(offset, offset + reads.pageSize);
        const result = await reads.revalidate(ids, context);
        context.signal.throwIfAborted();
        const represented = [...result.messages.map((row) => row.id), ...result.absent_ids];
        const checked = new Set(represented);
        if (represented.length !== ids.length || checked.size !== ids.length || ids.some((id) => !checked.has(id))) {
          throw new Error("Message feed revalidation did not partition every requested ID.");
        }
        survivors.push(...result.messages);
      }
      return {
        messages: [...survivors, ...fresh],
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
export function messageFeedRows<TMessage extends OrderedMessage>(
  data: InfiniteData<MessageFeedPage<TMessage>, string | null> | undefined,
): TMessage[] {
  const seen = new Set<string>();
  return (data?.pages.flatMap((page) => page.messages) ?? [])
    // An unseen message can move between sequential window reads. Prefer its
    // later observation before applying order; the next refresh fixes ownership.
    .reverse()
    .filter((row) => {
      if (seen.has(row.id)) return false;
      seen.add(row.id);
      return true;
    })
    .sort((left, right) => left.feed_order_key < right.feed_order_key ? 1 : left.feed_order_key > right.feed_order_key ? -1 : 0);
}

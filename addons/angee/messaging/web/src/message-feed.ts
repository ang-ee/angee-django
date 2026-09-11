import { keysetFeedRows, type KeysetFeedPage, type KeysetFeedWindow, type KeysetFeedRevalidation, type DocumentData } from "@angee/refine";
import type { InfiniteData } from "@tanstack/react-query";
import type { ThreadTranscriptDocument, ThreadTranscriptRevalidateDocument, ThreadTranscriptRow } from "./documents";

/** The server owns chronology; the client compares opaque order keys only. */
export function messageFeedRows<TMessage extends Pick<ThreadTranscriptRow, "id" | "feed_order_key">>(
  data: InfiniteData<KeysetFeedPage<TMessage>, string | null> | undefined,
): TMessage[] {
  return keysetFeedRows(data, (left, right) =>
    left.feed_order_key < right.feed_order_key ? 1 : left.feed_order_key > right.feed_order_key ? -1 : 0,
  );
}

/** Adapt Messaging's wire vocabulary to the schema-independent feed owner. */
export function messageFeedWindow<TMessage extends { id: string }>(
  page: Omit<DocumentData<typeof ThreadTranscriptDocument>["thread_message_feed"], "messages"> & { messages: readonly TMessage[] },
): KeysetFeedWindow<TMessage> {
  const { messages, ...window } = page;
  return { ...window, rows: messages };
}

export function messageFeedRevalidation<TMessage extends { id: string }>(
  page: Omit<DocumentData<typeof ThreadTranscriptRevalidateDocument>["thread_message_feed_revalidate"], "messages"> & { messages: readonly TMessage[] },
): KeysetFeedRevalidation<TMessage> {
  const { messages, ...revalidation } = page;
  return { ...revalidation, rows: messages };
}

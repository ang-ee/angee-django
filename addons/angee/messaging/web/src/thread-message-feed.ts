import { useAuth } from "@angee/app";
import {
  authoredQueryKey,
  requestAuthoredData,
  sharedAuthoredMeta,
  useActiveDataProviderName,
  useAuthoredErrorPolicy,
  useAuthoredLiveInterest,
  type DocumentData,
} from "@angee/refine";
import { useDataProvider } from "@refinedev/core";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";

import { ThreadTranscriptDocument, ThreadTranscriptRevalidateDocument } from "./documents";
import { messageFeedOptions } from "./message-feed";

// Message/Reaction writes change content; the other owners supply root access,
// sender identity and attachment metadata. Immutable fragments and readonly
// parts have no independent change root. Reaction retains local-write interest.
const MESSAGE_MODELS = [
  "messaging.Message", "messaging.Reaction", "messaging.Thread",
  "parties.Handle", "parties.Party", "storage.File",
] as const;

/** The thread owns its scope and full transcript projection; Query owns state. */
export function useThreadMessageFeed(threadId: string) {
  const client = useQueryClient();
  const dataProvider = useDataProvider();
  const provider = useActiveDataProviderName() ?? "default";
  const actor = useAuth().user?.id;
  const enabled = Boolean(actor && threadId);
  const queryKey = ["angee", "authored", "message-feed", actor,
    authoredQueryKey(ThreadTranscriptDocument, { threadId, limit: 50 }, provider)] as const;
  const configured = messageFeedOptions(client, {
    queryKey,
    pageSize: 50,
    async window(beforeCursor, throughCursor, limit, context) {
      const data = await requestAuthoredData<DocumentData<typeof ThreadTranscriptDocument>>(
        dataProvider, provider, ThreadTranscriptDocument,
        { threadId, beforeCursor, throughCursor, limit }, context,
      );
      return data.thread_message_feed;
    },
    async revalidate(ids, context) {
      const data = await requestAuthoredData<DocumentData<typeof ThreadTranscriptRevalidateDocument>>(
        dataProvider, provider, ThreadTranscriptRevalidateDocument, { threadId, ids }, context,
      );
      return data.thread_message_feed_revalidate;
    },
  });
  useAuthoredLiveInterest(enabled, MESSAGE_MODELS);
  useAuthoredErrorPolicy([queryKey]);
  return useInfiniteQuery({ ...configured, enabled, meta: sharedAuthoredMeta(client, queryKey, MESSAGE_MODELS) });
}

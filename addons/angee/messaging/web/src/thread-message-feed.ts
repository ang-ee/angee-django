import { useAuth } from "@angee/app";
import { useAuthoredKeysetFeed } from "@angee/refine";
import { ThreadTranscriptDocument, ThreadTranscriptRevalidateDocument } from "./documents";
import { messageFeedWindow, messageFeedRevalidation } from "./message-feed";

// Message/Reaction writes change content; the other owners supply root access,
// sender identity and attachment metadata. Immutable fragments and readonly
// parts have no independent change root. Reaction retains local-write interest.
const MESSAGE_MODELS = [
  "messaging.Message", "messaging.Reaction", "messaging.Thread",
  "parties.Handle", "parties.Party", "storage.File",
] as const;

/** The thread owns its scope and full transcript projection; Query owns state. */
export function useThreadMessageFeed(threadId: string) {
  return useAuthoredKeysetFeed({
    actor: useAuth().user?.id,
    enabled: Boolean(threadId),
    models: MESSAGE_MODELS,
    pageSize: 50,
    window: {
      document: ThreadTranscriptDocument,
      variables: (beforeCursor, throughCursor, limit) => ({ threadId, beforeCursor, throughCursor, limit }),
      select: (data) => messageFeedWindow(data.thread_message_feed),
    },
    revalidate: {
      document: ThreadTranscriptRevalidateDocument,
      variables: (ids) => ({ threadId, ids }),
      select: (data) => messageFeedRevalidation(data.thread_message_feed_revalidate),
    },
  });
}

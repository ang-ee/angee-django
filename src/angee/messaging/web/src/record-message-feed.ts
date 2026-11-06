import { useAuth } from "@angee/app";
import { useModelMetadata } from "@angee/metadata";
import { useAuthoredKeysetFeed } from "@angee/refine";
import { RecordMessageFeedDocument, RecordMessageFeedRevalidateDocument, READ_MODELS } from "./documents";
import { messageFeedWindow, messageFeedRevalidation } from "./message-feed";

/** Private chatter keeps the parent as its live/read boundary; Query owns retained pages. */
export function useRecordMessageFeed(options: {
  modelLabel: string; recordId: string; search: string; messageTypes: readonly string[];
  around?: string; pageSize: number; enabled: boolean;
}) {
  const { modelLabel, recordId, search, messageTypes, around, pageSize, enabled } = options;
  const model = useModelMetadata(modelLabel)?.resource.canonicalLabel ?? modelLabel;
  const scope = { modelLabel, recordId, search, messageTypes: [...messageTypes] };
  return useAuthoredKeysetFeed({
    actor: useAuth().user?.id,
    enabled,
    models: [...READ_MODELS, model],
    records: [{ model, id: recordId }],
    pageSize,
    window: {
      document: RecordMessageFeedDocument,
      variables: (beforeCursor, throughCursor, limit) => ({ ...scope, around, beforeCursor, throughCursor, limit }),
      newerVariables: (afterCursor, limit) => ({ ...scope, around, afterCursor, limit }),
      select: (data) => messageFeedWindow(data.record_message_feed),
    },
    revalidate: {
      models: ["messaging.Message"],
      document: RecordMessageFeedRevalidateDocument,
      variables: (ids) => ({ ...scope, ids }),
      select: (data) => messageFeedRevalidation(data.record_message_feed_revalidate),
    },
  });
}

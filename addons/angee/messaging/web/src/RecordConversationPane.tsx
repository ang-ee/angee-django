import { EmptyState, LoadingPanel, type ChatterViewContext } from "@angee/ui";
import type { ReactElement } from "react";

import { useMessagingT } from "./i18n";
import { ThreadTranscript } from "./ThreadTranscript";
import { useRecordSourceThreads } from "./useRecordSourceThreads";

export interface RecordConversationPaneProps {
  context: ChatterViewContext;
  attachmentId?: string;
}

/** An attachment drill-in resolves its thread through the source-edge read gate. */
export function RecordConversationPane({ context, attachmentId }: RecordConversationPaneProps): ReactElement {
  const t = useMessagingT();
  const query = useRecordSourceThreads(context);
  if (query.isFetching && !query.data) return <LoadingPanel message={t("transcript.loading")} />;
  const thread = query.data?.record_source_threads.find((edge) => edge.id === attachmentId)?.thread;
  if (query.error || !thread) {
    return <EmptyState icon="channel" className="min-h-48 p-4"
      title={t(query.error ? "transcript.error" : "conversations.unavailable")} />;
  }
  return <ThreadTranscript key={thread.id} threadId={thread.id} order="history" />;
}

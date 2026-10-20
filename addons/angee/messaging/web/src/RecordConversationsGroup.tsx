import { PaneRow, PaneRows, formatDate, useResourceRecordHrefLookup, type ChatterViewContext } from "@angee/ui";
import * as React from "react";

import { useMessagingT } from "./i18n";
import { useRecordSourceThreads } from "./useRecordSourceThreads";

const THREAD_MODEL = "messaging.Thread";

/** The Related group's count: the conversations this record came from. */
export function useRecordConversationsCount(context: ChatterViewContext): number | undefined {
  return useRecordSourceThreads(context).data?.record_source_threads.length;
}

/** The conversations the record came from (source-role thread attachments), each a row to its thread. */
export function RecordConversationsGroup({ context }: { context: ChatterViewContext }): React.ReactElement | null {
  const t = useMessagingT();
  const query = useRecordSourceThreads(context);
  const href = useResourceRecordHrefLookup();
  const rows = query.data?.record_source_threads ?? [];
  // A Related group with no rows and no verb is absent, not an empty note.
  if (rows.length === 0) return null;
  return (
    <PaneRows>
      {rows.map((edge) => {
        const thread = edge.thread;
        if (!thread) return null;
        const to = href(THREAD_MODEL, thread.id);
        return (
          <li key={edge.id}>
            <PaneRow
              icon="channel"
              title={thread.title?.text || edge.label || t("conversations.untitled")}
              detail={t("conversations.messages", { count: thread.message_count })}
              meta={thread.last_message_at ? formatDate(thread.last_message_at) : undefined}
              href={to}
              label={t("conversations.open")}
            />
          </li>
        );
      })}
    </PaneRows>
  );
}

import { useAuthoredQuery } from "@angee/refine";
import { PaneRow, PaneRows, formatDate, useResourceRecordHrefLookup, type ChatterViewContext } from "@angee/ui";
import { useNavigate } from "@tanstack/react-router";
import * as React from "react";

import { RecordSourceThreadsDocument, THREAD_ATTACHMENT_MODEL } from "./documents";

const THREAD_MODEL = "messaging.Thread";
import { useMessagingT } from "./i18n";

const MODELS = [THREAD_ATTACHMENT_MODEL, THREAD_MODEL] as const;

function target(context: ChatterViewContext): { modelLabel: string; recordId: string } {
  return {
    modelLabel: context.route?.canonicalLabel ?? context.route?.modelLabel ?? "",
    recordId: context.view.kind === "record" ? context.view.sqid ?? "" : "",
  };
}

function useSourceThreads(context: ChatterViewContext) {
  const { modelLabel, recordId } = target(context);
  const variables = React.useMemo(() => ({ modelLabel, recordId }), [modelLabel, recordId]);
  return useAuthoredQuery(RecordSourceThreadsDocument, variables, {
    enabled: Boolean(modelLabel && recordId), models: MODELS,
  });
}

/** The Related group's count: the conversations this record came from. */
export function useRecordConversationsCount(context: ChatterViewContext): number | undefined {
  return useSourceThreads(context).data?.record_source_threads.length;
}

/** The conversations the record came from (source-role thread attachments), each a row to its thread. */
export function RecordConversationsGroup({ context }: { context: ChatterViewContext }): React.ReactElement {
  const t = useMessagingT();
  const query = useSourceThreads(context);
  const href = useResourceRecordHrefLookup();
  const navigate = useNavigate();
  const rows = query.data?.record_source_threads ?? [];
  if (rows.length === 0) return <p className="px-1 py-1 text-13 text-fg-muted">{t("conversations.empty")}</p>;
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
              onOpen={to ? () => void navigate({ href: to } as never) : undefined}
              label={t("conversations.open")}
            />
          </li>
        );
      })}
    </PaneRows>
  );
}

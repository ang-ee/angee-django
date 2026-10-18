import { useAuthoredQuery } from "@angee/refine";
import {
  MessagePartsView,
  RecordReference,
  type ActivityEntry,
  type ActivityEntrySource,
  type ActivityRecord,
  type ActivityWindow,
  type ChatterViewContext,
} from "@angee/ui";
import * as React from "react";

import {
  READ_MODELS,
  RecordSourceThreadsDocument,
  RecordThreadDocument,
  THREAD_ATTACHMENT_MODEL,
  THREAD_CHATTER_EDGE,
} from "./documents";
import { RecordChatterPane } from "./RecordChatterPane";
import { useMessagingT } from "./i18n";

export function recordCommentActivitySource(submitKey: "enter" | "mod-enter" = "enter"): ActivityEntrySource {
  return {
    kinds: ["comment"],
    useEntries: (record, window) => useRecordCommentEntries(record, window, submitKey),
  };
}

export const recordConversationActivitySource: ActivityEntrySource = {
  kinds: ["conversation"],
  useEntries: useRecordConversationEntries,
};

function useRecordCommentEntries(
  record: ActivityRecord,
  window: ActivityWindow,
  submitKey: "enter" | "mod-enter",
): readonly ActivityEntry[] {
  const t = useMessagingT();
  const enabled = record.recordEdges.includes(THREAD_CHATTER_EDGE);
  const variables = React.useMemo(() => ({
    modelLabel: record.model,
    recordId: record.id,
    search: "",
    messageLimit: window.limit,
    messageTypes: [] as string[],
  }), [record.id, record.model, window.limit]);
  const query = useAuthoredQuery(RecordThreadDocument, variables, { enabled, models: READ_MODELS });
  const context = React.useMemo<ChatterViewContext>(() => ({
    pathname: "",
    params: {},
    route: {
      name: "activity",
      path: "",
      viewType: "record",
      modelLabel: record.model,
      canonicalLabel: record.model,
      recordEdges: record.recordEdges,
    },
    view: { kind: "record", type: "record", sqid: record.id },
  }), [record.id, record.model, record.recordEdges]);
  const composer = React.useMemo(() => enabled
    ? <RecordChatterPane context={context} submitKey={submitKey} composerOnly messageLimit={window.limit} />
    : null, [context, enabled, submitKey, window.limit]);
  React.useEffect(() => {
    window.publishComposer(composer);
    return () => window.publishComposer(null);
  }, [composer, window.publishComposer]);
  return React.useMemo(() => (query.data?.record_thread?.messages ?? []).map((message) => ({
    at: message.sent_at ?? message.created_at,
    kind: "comment" as const,
    actor: message.author_label || message.sender?.display_name || message.sender?.value || t("message.author"),
    title: message.subtype?.name || t("activity.comment"),
    body: <MessagePartsView parts={message.parts} />,
    reference: message.parent ? t("message.replyingTo", {
      kind: message.parent.subtype?.name || message.parent.message_type.toLowerCase(),
    }) : null,
  })), [query.data?.record_thread?.messages, t]);
}

function useRecordConversationEntries(record: ActivityRecord, window: ActivityWindow): readonly ActivityEntry[] {
  const t = useMessagingT();
  const enabled = record.recordEdges.includes(THREAD_ATTACHMENT_MODEL);
  const variables = React.useMemo(() => ({ modelLabel: record.model, recordId: record.id }),
    [record.id, record.model]);
  const query = useAuthoredQuery(RecordSourceThreadsDocument, variables, { enabled, models: READ_MODELS });
  return React.useMemo(() => (query.data?.record_source_threads ?? []).flatMap((row) => row.thread ? [{
    at: row.thread.last_message_at ?? row.created_at,
    kind: "conversation" as const,
    actor: null,
    title: row.thread.title?.text || row.label || t("sources.conversation"),
    body: t("activity.conversationMessages", { count: row.thread.message_count }),
    reference: <RecordReference model="messaging.Thread" id={row.thread.id}
      label={row.thread.title?.text || row.label || t("sources.conversation")} />,
  }] : []).slice(0, window.limit), [query.data?.record_source_threads, t, window.limit]);
}

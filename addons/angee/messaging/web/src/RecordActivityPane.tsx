import { useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import { EmptyState, LoadingPanel, cn, formatDateTime, textRoleVariants } from "@angee/ui";
import type { ChatterViewContext } from "@angee/ui/runtime";

import { useMessagingT } from "./i18n";
import { READ_MODELS, RecordActivityThreadDocument, type RecordActivityMessageRow } from "./documents";

export interface RecordActivityPaneProps {
  context: ChatterViewContext;
}

/** The Activity chatter tab: the record's retained system changes. It reads a
 * kind-filtered record-thread window, leaving ordinary conversation to Comments
 * while reusing the same stored thread; tasks about the record live in their
 * owner's own tab. */
export function RecordActivityPane({ context }: RecordActivityPaneProps): React.ReactElement {
  const t = useMessagingT();
  const modelLabel = context.route?.modelLabel;
  const recordId = context.view.kind === "record" ? context.view.sqid : undefined;
  const enabled = Boolean(modelLabel && recordId);
  const variables = React.useMemo(
    () => ({ modelLabel: modelLabel ?? "", recordId: recordId ?? "" }),
    [modelLabel, recordId],
  );
  const threadQuery = useAuthoredQuery(RecordActivityThreadDocument, variables, {
    enabled,
    models: READ_MODELS,
  });
  const threadPayload = threadQuery.data?.record_thread;
  const updates = threadPayload?.messages ?? [];

  if (!enabled) {
    return (
      <EmptyState
        icon="activity"
        title={t("activity.noRecord")}
        description={t("activity.noRecordHint")}
        className="min-h-48 p-4"
      />
    );
  }
  if (threadQuery.isFetching && threadQuery.data === undefined) {
    return <LoadingPanel message={t("activity.loading")} />;
  }
  if (threadQuery.error || threadPayload?.error_code === "BAD_RECORD") {
    return (
      <EmptyState
        icon="activity"
        title={t("activity.disabled")}
        description={t("activity.disabledHint")}
        className="min-h-48 p-4"
      />
    );
  }
  if (updates.length === 0) {
    return (
      <EmptyState
        icon="activity"
        title={t("activity.emptyTitle")}
        description={t("activity.emptyHint")}
        className="min-h-40 p-4"
      />
    );
  }

  return (
    <section className="space-y-2" aria-label={t("activity.history")}>
      <h3 className={cn(textRoleVariants({ role: "meta" }), "text-fg-muted")}>{t("activity.history")}</h3>
      {updates.map((message) => (
        <RecordUpdateItem key={message.id} message={message} />
      ))}
      {(threadPayload?.message_result_count ?? 0) > updates.length ? (
        <p className={cn(textRoleVariants({ role: "caption" }), "text-fg-muted")}>
          {t("activity.historyLimited", { count: updates.length })}
        </p>
      ) : null}
    </section>
  );
}

function RecordUpdateItem({
  message,
}: {
  message: RecordActivityMessageRow;
}): React.ReactElement {
  const t = useMessagingT();
  const author = message.sender?.display_name || message.sender?.value || t("message.author");
  const timestamp = message.sent_at ?? message.created_at;
  const trackingValues = [...message.tracking_values].sort(
    (left, right) =>
      left.position - right.position || left.field_label.localeCompare(right.field_label),
  );

  return (
    <article className="space-y-2 rounded-8 border border-border-subtle bg-surface p-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className={cn(textRoleVariants({ role: "meta" }), "truncate")}>{author}</span>
        {timestamp ? (
          <time className={cn(textRoleVariants({ role: "caption" }), "shrink-0 text-fg-muted")}>
            {formatDateTime(timestamp)}
          </time>
        ) : null}
      </div>
      {message.preview ? <p className="text-13 text-fg">{message.preview}</p> : null}
      {trackingValues.length > 0 ? (
        <dl className="space-y-1 rounded-6 bg-surface-inset p-2">
          {trackingValues.map((tracking) => (
            <div
              key={tracking.id}
              className="grid grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] gap-2 text-13"
            >
              <dt className="truncate font-medium text-fg-muted">{tracking.field_label}</dt>
              <dd className="min-w-0 text-fg">
                <span className="text-fg-muted">{tracking.old_display || "—"}</span>
                <span aria-hidden="true"> → </span>
                <span>{tracking.new_display || "—"}</span>
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
    </article>
  );
}

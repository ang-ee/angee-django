import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import { Button, DatePopover, EmptyState, ErrorBanner, FieldRoot, Glyph, LoadingPanel, Textarea, cn, dateFromValue, errorMessage, formatDate, formatDateStorage, formatDateTime, textRoleVariants, useActionForm, useUiT, useWatch, type ResolverResult } from "@angee/ui";
import type { ChatterViewContext } from "@angee/ui/runtime";
import { userDisplayName } from "@angee/iam";

import { activityStateLabel } from "./activity-state";
import { useMessagingT } from "./i18n";
import {
  CancelRecordActivityDocument,
  CompleteRecordActivityDocument,
  READ_MODELS,
  RecordActivityThreadDocument,
  ScheduleRecordActivityDocument,
  type RecordActivityMessageRow,
  type RecordActivityRow,
} from "./documents";

export interface RecordActivityPaneProps {
  context: ChatterViewContext;
}

/** The activity-scheduler form's collected values, fired through `useActionForm`. */
interface ScheduleValues {
  summary: string;
  note: string;
  dueDate: string;
}

/** The Activity chatter tab: retained system changes, scheduled activities, and a
 * scheduler. It reads a kind-filtered record-thread window, leaving ordinary
 * conversation to Comments while reusing the same stored thread. */
export function RecordActivityPane({ context }: RecordActivityPaneProps): React.ReactElement {
  const t = useMessagingT();
  const uiT = useUiT();
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
  const [scheduleActivity] = useAuthoredMutation(ScheduleRecordActivityDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.schedule_record_activity,
  });
  const [completeActivity, completeState] = useAuthoredMutation(CompleteRecordActivityDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.complete_record_activity,
  });
  const [cancelActivity, cancelState] = useAuthoredMutation(CancelRecordActivityDocument, {
    invalidateModels: READ_MODELS,
    errorFrom: (data) => data?.cancel_record_activity,
  });
  const [dateOpen, setDateOpen] = React.useState(false);
  const [feedbackById, setFeedbackById] = React.useState<Record<string, string>>({});
  // `error` carries the imperative complete/cancel failures; the schedule form's
  // own lifecycle (busy + failure + reset) is owned by `useActionForm` below.
  const [error, setError] = React.useState<string | null>(null);
  // The schedule form composes the shared action-form lifecycle: fire the authored
  // mutation, surface a thrown failure as `formError`, and clear the fields on
  // success. No toast — the scheduled activity appears in the list via invalidation.
  const scheduleForm = useActionForm<ScheduleValues>({
    defaultValues: { summary: "", note: "", dueDate: "" },
    resolver: (values): ResolverResult<ScheduleValues> => {
      const summary = values.summary.trim();
      return summary
        ? { values: { ...values, summary }, errors: {} }
        : { values: {}, errors: { summary: { type: "required", message: uiT("form.required") } } };
    },
    submit: async ({ summary, note, dueDate }) => {
      await scheduleActivity({
        modelLabel: modelLabel ?? "",
        recordId: recordId ?? "",
        summary,
        note,
        dueDate: dueDate || null,
        activityType: "todo",
      });
      return { status: "ok", data: undefined };
    },
    onSuccess: () => {
      scheduleForm.form.reset();
    },
    toastSuccess: false,
    genericErrorMessage: t("activity.errorSchedule"),
  });
  const dueDate = useWatch({ control: scheduleForm.form.control, name: "dueDate" });
  const summaryError = scheduleForm.form.formState.errors.summary;
  const threadPayload = threadQuery.data?.record_thread;
  const activities = React.useMemo(
    () => [...(threadPayload?.activities ?? [])].sort(compareActivities),
    [threadPayload?.activities],
  );
  const updates = threadPayload?.messages ?? [];
  const busy = scheduleForm.submitting || completeState.fetching || cancelState.fetching;

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

  async function handleSchedule(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!modelLabel || !recordId) return;
    await scheduleForm.run();
  }

  async function handleComplete(activityId: string): Promise<void> {
    setError(null);
    try {
      await completeActivity({
        activityId,
        feedback: feedbackById[activityId] ?? "",
      });
      setFeedbackById((current) => {
        const { [activityId]: _removed, ...rest } = current;
        return rest;
      });
    } catch (cause) {
      setError(errorMessage(cause, t("activity.errorComplete")));
    }
  }

  async function handleCancel(activityId: string): Promise<void> {
    setError(null);
    try {
      await cancelActivity({ activityId });
    } catch (cause) {
      setError(errorMessage(cause, t("activity.errorCancel")));
    }
  }

  const dueDateLabel = dueDate ? formatDate(dueDate) : t("activity.noDueDate");

  return (
    <div className="flex min-h-72 flex-col gap-4">
      {updates.length > 0 ? (
        <section className="space-y-2" aria-label={t("activity.history")}>
          <h3 className={cn(textRoleVariants({ role: "meta" }), "text-fg-muted")}>
            {t("activity.history")}
          </h3>
          {updates.map((message) => (
            <RecordUpdateItem key={message.id} message={message} />
          ))}
          {(threadPayload?.message_result_count ?? 0) > updates.length ? (
            <p className={cn(textRoleVariants({ role: "caption" }), "text-fg-muted")}>
              {t("activity.historyLimited", { count: updates.length })}
            </p>
          ) : null}
        </section>
      ) : null}
      {activities.length > 0 ? (
        <section className="space-y-3" aria-label={t("activity.planned")}>
          <h3 className={cn(textRoleVariants({ role: "meta" }), "text-fg-muted")}>
            {t("activity.planned")}
          </h3>
          {activities.map((activity) => (
            <ActivityItem
              key={activity.id}
              activity={activity}
              busy={busy}
              feedback={feedbackById[activity.id] ?? ""}
              onFeedback={(value) =>
                setFeedbackById((current) => ({ ...current, [activity.id]: value }))
              }
              onComplete={() => void handleComplete(activity.id)}
              onCancel={() => void handleCancel(activity.id)}
            />
          ))}
        </section>
      ) : updates.length === 0 ? (
        <EmptyState
          icon="activity"
          title={t("activity.emptyTitle")}
          description={t("activity.emptyHint")}
          className="min-h-40 p-4"
        />
      ) : null}
      <form
        onSubmit={handleSchedule}
        className="mt-auto space-y-2 border-t border-border-subtle pt-3"
      >
        <FieldRoot invalid={Boolean(summaryError)}>
          <FieldRoot.Label className="sr-only">{t("activity.summary")}</FieldRoot.Label>
          <FieldRoot.Control
            {...scheduleForm.form.register("summary")}
            placeholder={t("activity.summary")}
          />
          {summaryError ? <FieldRoot.Error match>{summaryError.message}</FieldRoot.Error> : null}
        </FieldRoot>
        <Textarea
          {...scheduleForm.form.register("note")}
          rows={2}
          resize="none"
          aria-label={t("activity.notes")}
          placeholder={t("activity.notes")}
        />
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <DatePopover
              selected={dateFromValue(dueDate)}
              label={dueDateLabel}
              ariaLabel={t("activity.dueDate")}
              open={dateOpen}
              onOpenChange={setDateOpen}
              onSelectDate={(date) => scheduleForm.form.setValue("dueDate", formatDateStorage(date) ?? "", { shouldDirty: true })}
              footer={
                dueDate ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="mt-2 w-full"
                    onClick={() => {
                      scheduleForm.form.setValue("dueDate", "", { shouldDirty: true });
                      setDateOpen(false);
                    }}
                  >
                    <Glyph name="x" />
                    {t("activity.clearDueDate")}
                  </Button>
                ) : null
              }
            />
          </div>
          <Button
            type="submit"
            variant="primary"
            size="sm"
            disabled={scheduleForm.submitting}
          >
            <Glyph name="calendar" />
            {t("activity.schedule")}
          </Button>
        </div>
        {(scheduleForm.formError ?? error) ? (
          <ErrorBanner description={scheduleForm.formError ?? error} />
        ) : null}
      </form>
    </div>
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

function ActivityItem({
  activity,
  busy,
  feedback,
  onFeedback,
  onComplete,
  onCancel,
}: {
  activity: RecordActivityRow;
  busy: boolean;
  feedback: string;
  onFeedback: (value: string) => void;
  onComplete: () => void;
  onCancel: () => void;
}): React.ReactElement {
  const t = useMessagingT();
  const closed = activity.status !== "TODO";
  return (
    <article className="rounded-6 border border-border-subtle bg-surface p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-2">
            <Glyph
              decorative
              name={closed ? "circle-check" : "activity"}
              className="shrink-0 text-fg-muted"
            />
            <h3 className="truncate text-13 font-medium text-fg">{activity.summary}</h3>
          </div>
          <p className={cn(textRoleVariants({ role: "caption" }), "pl-6")}>
            {[
              activity.user ? userDisplayName(activity.user, "") : "",
              activity.due_date ? formatDate(activity.due_date) : "",
              activityStateLabel(activity, t),
            ].filter(Boolean).join(" · ")}
          </p>
        </div>
        {!closed ? (
          <Button
            type="button"
            variant="ghost"
            size="iconSm"
            disabled={busy}
            onClick={onCancel}
            aria-label={t("activity.cancel")}
          >
            <Glyph name="trash" />
          </Button>
        ) : null}
      </div>
      {activity.note ? (
        <p className="mt-2 whitespace-pre-wrap text-13 text-fg-muted">{activity.note}</p>
      ) : null}
      {activity.feedback ? (
        <p className="mt-2 whitespace-pre-wrap rounded-6 bg-surface-inset p-2 text-13 text-fg-muted">
          {activity.feedback}
        </p>
      ) : null}
      {!closed ? (
        <div className="mt-3 space-y-2">
          <Textarea
            value={feedback}
            onChange={(event) => onFeedback(event.currentTarget.value)}
            rows={2}
            resize="none"
            aria-label={t("activity.feedback")}
            placeholder={t("activity.feedback")}
          />
          <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={onComplete}>
            <Glyph name="check" />
            {t("activity.markDone")}
          </Button>
        </div>
      ) : null}
    </article>
  );
}

function compareActivities(left: RecordActivityRow, right: RecordActivityRow): number {
  return (
    activityRank(left) - activityRank(right) ||
    dateValue(left.due_date) - dateValue(right.due_date) ||
    left.summary.localeCompare(right.summary)
  );
}

function activityRank(activity: RecordActivityRow): number {
  return activity.status === "TODO" ? 0 : 1;
}

function dateValue(value: string | null | undefined): number {
  return value ? Date.parse(value) : Number.MAX_SAFE_INTEGER;
}

import { useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import { Button, DatePopover, EmptyState, FieldRoot, Glyph, LoadingPanel, Textarea, cn, dateFromValue, errorMessage, formatDate, formatDateStorage, textRoleVariants } from "@angee/ui";
import type { ChatterViewContext } from "@angee/ui/runtime";

import { useMessagingT } from "./i18n";
import {
  CancelRecordActivityDocument,
  CompleteRecordActivityDocument,
  READ_MODELS,
  RecordActivityThreadDocument,
  ScheduleRecordActivityDocument,
  type RecordActivityRow,
} from "./documents";

export interface RecordActivityPaneProps {
  context: ChatterViewContext;
}

/** The Activity chatter tab: the record's scheduled activities plus a scheduler.
 *  Reads its own narrow `record_thread` window (activities only) so opening the tab
 *  never pulls the Comments feed payload. */
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
  const [scheduleActivity, scheduleState] = useAuthoredMutation(ScheduleRecordActivityDocument, {
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
  const [summary, setSummary] = React.useState("");
  const [note, setNote] = React.useState("");
  const [dueDate, setDueDate] = React.useState("");
  const [dateOpen, setDateOpen] = React.useState(false);
  const [feedbackById, setFeedbackById] = React.useState<Record<string, string>>({});
  const [error, setError] = React.useState<string | null>(null);
  const threadPayload = threadQuery.data?.record_thread;
  const activities = React.useMemo(
    () => [...(threadPayload?.activities ?? [])].sort(compareActivities),
    [threadPayload?.activities],
  );
  const busy = scheduleState.fetching || completeState.fetching || cancelState.fetching;

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
  if (threadQuery.fetching && threadQuery.data === undefined) {
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
    const nextSummary = summary.trim();
    if (!nextSummary || !modelLabel || !recordId) return;
    setError(null);
    try {
      await scheduleActivity({
        modelLabel,
        recordId,
        summary: nextSummary,
        note,
        dueDate: dueDate || null,
        activityType: "todo",
      });
      setSummary("");
      setNote("");
      setDueDate("");
    } catch (cause) {
      setError(errorMessage(cause, t("activity.errorSchedule")));
    }
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
      {activities.length > 0 ? (
        <div className="space-y-3">
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
        </div>
      ) : (
        <EmptyState
          icon="activity"
          title={t("activity.emptyTitle")}
          description={t("activity.emptyHint")}
          className="min-h-40 p-4"
        />
      )}
      <form
        onSubmit={handleSchedule}
        className="mt-auto space-y-2 border-t border-border-subtle pt-3"
      >
        <FieldRoot>
          <FieldRoot.Label className="sr-only">{t("activity.summary")}</FieldRoot.Label>
          <FieldRoot.Control
            value={summary}
            onChange={(event) => setSummary(event.currentTarget.value)}
            placeholder={t("activity.summary")}
          />
        </FieldRoot>
        <Textarea
          value={note}
          onChange={(event) => setNote(event.currentTarget.value)}
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
              onSelectDate={(date) => setDueDate(formatDateStorage(date) ?? "")}
              footer={
                dueDate ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="mt-2 w-full"
                    onClick={() => {
                      setDueDate("");
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
            disabled={scheduleState.fetching || summary.trim() === ""}
          >
            <Glyph name="calendar" />
            {t("activity.schedule")}
          </Button>
        </div>
        {error ? (
          <p className={cn(textRoleVariants({ role: "caption" }), "text-danger-text")}>{error}</p>
        ) : null}
      </form>
    </div>
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
            {activity.user.display_name || activity.user.username}
            {activity.due_date ? ` · ${formatDate(activity.due_date)}` : ""}
            {" · "}
            {activityStateLabel(activity, t)}
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

function activityStateLabel(
  activity: RecordActivityRow,
  t: ReturnType<typeof useMessagingT>,
): string {
  if (activity.status === "DONE") return t("activity.stateDone");
  if (activity.status === "CANCELED") return t("activity.stateCanceled");
  if (activity.state === "overdue") return t("activity.stateOverdue");
  if (activity.state === "today") return t("activity.stateToday");
  return t("activity.statePlanned");
}

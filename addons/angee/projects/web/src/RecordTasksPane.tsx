import { extractActionOutcome, useAuthoredMutation, useAuthoredQuery } from "@angee/refine";
import * as React from "react";
import {
  Button,
  DatePopover,
  EmptyState,
  ErrorBanner,
  FieldRoot,
  Glyph,
  LoadingPanel,
  SectionHeading,
  SegmentedControl,
  Select,
  Textarea,
  actionOutcomeSubmitResult,
  cn,
  dateFromValue,
  formatDate,
  formatDateStorage,
  textRoleVariants,
  useActionForm,
  useActionOutcomeMutation,
  useActionResultRun,
  useUiT,
  useWatch,
  type ResolverResult,
} from "@angee/ui";
import type { ActionFieldName } from "@angee/gql/console/actions";
import type { ChatterViewContext } from "@angee/ui/runtime";

import {
  LogRecordTaskDocument,
  RECORD_TASK_MODELS,
  RecordTasksDocument,
  ScheduleRecordTaskDocument,
  type ActivityTypeRow,
  type RecordTaskRow,
} from "./documents";
import { useProjectsT } from "./i18n";
import { isOpenTask, offersTaskAction } from "./task-actions";

export interface RecordTasksPaneProps {
  context: ChatterViewContext;
}

type Mode = "schedule" | "log";

/** The scheduler/log form's collected values, fired through `useActionForm`. An
 * empty `activityType` schedules the catalog's default kind. */
interface TaskFormValues {
  text: string;
  note: string;
  day: string;
  activityType: string;
}

const EMPTY_FORM: TaskFormValues = { text: "", note: "", day: "", activityType: "" };

/** The Tasks chatter tab: the record's tasks, a scheduler, and a log for exchanges
 * that already happened. The task schema derives access from the record; the
 * generic task verbs complete and drop, and a completion posts on the record. */
export function RecordTasksPane({ context }: RecordTasksPaneProps): React.ReactElement {
  const t = useProjectsT();
  const uiT = useUiT();
  const modelLabel = context.route?.modelLabel;
  const recordId = context.view.kind === "record" ? context.view.sqid : undefined;
  const enabled = Boolean(modelLabel && recordId);
  const variables = React.useMemo(
    () => ({ modelLabel: modelLabel ?? "", recordId: recordId ?? "" }),
    [modelLabel, recordId],
  );
  const tasksQuery = useAuthoredQuery(RecordTasksDocument, variables, { enabled, models: RECORD_TASK_MODELS });
  const [scheduleTask] = useAuthoredMutation(ScheduleRecordTaskDocument, { invalidateModels: RECORD_TASK_MODELS });
  const [logTask] = useAuthoredMutation(LogRecordTaskDocument, { invalidateModels: RECORD_TASK_MODELS });
  // The generic task verbs close record tasks; the schema admits only the record's writers.
  const [completeTask, completeState] = useActionOutcomeMutation<ActionFieldName>("complete_task", {
    invalidateModels: RECORD_TASK_MODELS,
  });
  const [dropTask, dropState] = useActionOutcomeMutation<ActionFieldName>("drop_task", {
    invalidateModels: RECORD_TASK_MODELS,
  });
  const settle = useActionResultRun();
  const [mode, setMode] = React.useState<Mode>("schedule");
  const [dateOpen, setDateOpen] = React.useState(false);
  const [feedbackById, setFeedbackById] = React.useState<Record<string, string>>({});
  const form = useActionForm<TaskFormValues>({
    defaultValues: EMPTY_FORM,
    resolver: (values): ResolverResult<TaskFormValues> => {
      const text = values.text.trim();
      const errors: Record<string, { type: string; message: string }> = {};
      if (!text) errors.text = { type: "required", message: uiT("form.required") };
      if (mode === "log" && !values.day) errors.day = { type: "required", message: uiT("form.required") };
      if (mode === "log" && !values.activityType) {
        errors.activityType = { type: "required", message: uiT("form.required") };
      }
      return Object.keys(errors).length ? { values: {}, errors } : { values: { ...values, text }, errors: {} };
    },
    submit: async ({ text, note, day, activityType }) =>
      actionOutcomeSubmitResult(
        mode === "log"
          ? extractActionOutcome(
            await logTask({ ...variables, activityType, occurredOn: day, note: note ? `${text}\n${note}` : text }),
            "log_record_task",
          )
          : extractActionOutcome(
            await scheduleTask({
              ...variables, title: text, note, dueDate: day || null, activityType: activityType || null,
            }),
            "schedule_record_task",
          ),
      ),
    onSuccess: () => {
      form.form.reset(EMPTY_FORM);
    },
    toastSuccess: false,
    genericErrorMessage: mode === "log" ? t("recordTasks.errorLog") : t("recordTasks.errorSchedule"),
  });
  const day = useWatch({ control: form.form.control, name: "day" });
  const activityType = useWatch({ control: form.form.control, name: "activityType" });
  const textError = form.form.formState.errors.text;
  const dayError = form.form.formState.errors.day;
  const typeError = form.form.formState.errors.activityType;
  const kinds = tasksQuery.data?.activity_types ?? [];
  const kindByKey = React.useMemo(() => new Map(kinds.map((kind) => [kind.key, kind])), [kinds]);
  // The server orders them: open first, then by due date and title.
  const tasks = tasksQuery.data?.record_tasks ?? [];
  const open = tasks.filter(isOpenTask);
  const closed = tasks.filter((task) => !isOpenTask(task));
  const busy = form.submitting || completeState.fetching || dropState.fetching;

  if (!enabled) {
    return (
      <EmptyState
        icon="project-task"
        title={t("recordTasks.noRecord")}
        description={t("recordTasks.noRecordHint")}
        className="min-h-48 p-4"
      />
    );
  }
  if (tasksQuery.isFetching && tasksQuery.data === undefined) {
    return <LoadingPanel message={t("recordTasks.loading")} />;
  }
  if (tasksQuery.error) {
    return (
      <EmptyState
        icon="project-task"
        title={t("recordTasks.unavailable")}
        description={t("recordTasks.unavailableHint")}
        className="min-h-48 p-4"
      />
    );
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    await form.run();
  }

  async function handleComplete(id: string): Promise<void> {
    const outcome = await settle(() => completeTask(id, { feedback: feedbackById[id] ?? "" }));
    if (outcome?.ok) setFeedbackById(({ [id]: _removed, ...rest }) => rest);
  }

  async function handleCancel(id: string): Promise<void> {
    await settle(() => dropTask(id, { reason: "OBSOLETE" }));
  }

  const kindOptions = kinds.map((kind) => ({ value: kind.key, label: kind.name }));
  const dayLabel = day ? formatDate(day) : t("recordTasks.noDate");

  return (
    <div className="flex min-h-72 flex-col gap-4">
      {open.length > 0 ? (
        <TaskSection label={t("recordTasks.open")}>
          {open.map((task) => (
            <TaskItem
              key={task.id}
              task={task}
              kind={kindByKey.get(task.activity_type ?? "")}
              busy={busy}
              feedback={feedbackById[task.id] ?? ""}
              onFeedback={(value) => setFeedbackById((current) => ({ ...current, [task.id]: value }))}
              onComplete={() => void handleComplete(task.id)}
              onCancel={() => void handleCancel(task.id)}
            />
          ))}
        </TaskSection>
      ) : null}
      {closed.length > 0 ? (
        <TaskSection label={t("recordTasks.closed")}>
          {closed.map((task) => (
            <TaskItem key={task.id} task={task} kind={kindByKey.get(task.activity_type ?? "")} busy={busy} />
          ))}
        </TaskSection>
      ) : null}
      {tasks.length === 0 ? (
        <EmptyState
          icon="project-task"
          title={t("recordTasks.emptyTitle")}
          description={t("recordTasks.emptyHint")}
          className="min-h-40 p-4"
        />
      ) : null}
      <form onSubmit={handleSubmit} className="mt-auto space-y-2 border-t border-border-subtle pt-3">
        <SegmentedControl<Mode>
          aria-label={t("recordTasks.tab")}
          value={mode}
          onValueChange={(next) => setMode(next)}
          options={[
            { value: "schedule", label: t("recordTasks.mode.schedule") },
            { value: "log", label: t("recordTasks.mode.log") },
          ]}
        />
        <FieldRoot invalid={Boolean(textError)}>
          <FieldRoot.Label className="sr-only">
            {mode === "log" ? t("recordTasks.logNote") : t("recordTasks.title")}
          </FieldRoot.Label>
          <FieldRoot.Control
            {...form.form.register("text")}
            placeholder={mode === "log" ? t("recordTasks.logNote") : t("recordTasks.title")}
          />
          {textError ? <FieldRoot.Error match>{textError.message}</FieldRoot.Error> : null}
        </FieldRoot>
        <Textarea
          {...form.form.register("note")}
          rows={2}
          resize="none"
          aria-label={t("recordTasks.note")}
          placeholder={t("recordTasks.note")}
        />
        <div className="flex items-center gap-2">
          {kindOptions.length > 0 ? (
            <Select
              aria-label={t("recordTasks.type")}
              placeholder={t("recordTasks.type")}
              options={kindOptions}
              value={activityType}
              onValueChange={(value) => form.form.setValue("activityType", value, { shouldDirty: true })}
            />
          ) : null}
          <div className="min-w-0 flex-1">
            <DatePopover
              selected={dateFromValue(day)}
              label={dayLabel}
              ariaLabel={mode === "log" ? t("recordTasks.occurredOn") : t("recordTasks.dueDate")}
              open={dateOpen}
              onOpenChange={setDateOpen}
              onSelectDate={(date) => form.form.setValue("day", formatDateStorage(date) ?? "", { shouldDirty: true })}
              footer={
                day ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="mt-2 w-full"
                    onClick={() => {
                      form.form.setValue("day", "", { shouldDirty: true });
                      setDateOpen(false);
                    }}
                  >
                    <Glyph name="x" />
                    {t("recordTasks.clearDate")}
                  </Button>
                ) : null
              }
            />
          </div>
          <Button type="submit" variant="primary" size="sm" disabled={form.submitting}>
            <Glyph name={mode === "log" ? "check" : "calendar"} />
            {mode === "log" ? t("recordTasks.log") : t("recordTasks.schedule")}
          </Button>
        </div>
        {(dayError ?? typeError) ? (
          <p role="alert" className={cn(textRoleVariants({ role: "caption" }), "text-danger-text")}>
            {(dayError ?? typeError)?.message}
          </p>
        ) : null}
        {form.formError ? <ErrorBanner description={form.formError} /> : null}
      </form>
    </div>
  );
}

function TaskSection({ label, children }: { label: string; children: React.ReactNode }): React.ReactElement {
  return (
    <section className="space-y-3" aria-label={label}>
      <SectionHeading label={label} count={React.Children.count(children)} />
      {children}
    </section>
  );
}

function TaskItem({
  task,
  kind,
  busy,
  feedback,
  onFeedback,
  onComplete,
  onCancel,
}: {
  task: RecordTaskRow;
  kind: ActivityTypeRow | undefined;
  busy: boolean;
  feedback?: string;
  onFeedback?: (value: string) => void;
  onComplete?: () => void;
  onCancel?: () => void;
}): React.ReactElement {
  const t = useProjectsT();
  const isOpen = isOpenTask(task);
  // The task's own verbs admit each control: only the record's writers close its tasks.
  const offers = (action: string) => isOpen && offersTaskAction(task, action);
  const assignee = task.assignee ? task.assignee.display_name || task.assignee.username : "";
  return (
    <article className="rounded-6 border border-border-subtle bg-surface p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-2">
            <Glyph
              decorative
              name={isOpen ? kind?.glyph || "project-task" : "circle-check"}
              fallbackName="project-task"
              className="shrink-0 text-fg-muted"
            />
            <h3 className="truncate text-13 font-medium text-fg">{task.title}</h3>
          </div>
          <p className={cn(textRoleVariants({ role: "caption" }), "pl-6")}>
            {[kind?.name ?? "", assignee, task.due_date ? formatDate(task.due_date) : "", dueLabel(task, t)]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        {offers("drop") && onCancel ? (
          <Button
            type="button"
            variant="ghost"
            size="iconSm"
            disabled={busy}
            onClick={onCancel}
            aria-label={t("recordTasks.cancel")}
          >
            <Glyph name="trash" />
          </Button>
        ) : null}
      </div>
      {task.note ? <p className="mt-2 whitespace-pre-wrap text-13 text-fg-muted">{task.note}</p> : null}
      {offers("complete") && onFeedback && onComplete ? (
        <div className="mt-3 space-y-2">
          <Textarea
            value={feedback ?? ""}
            onChange={(event) => onFeedback(event.currentTarget.value)}
            rows={2}
            resize="none"
            aria-label={t("recordTasks.feedback")}
            placeholder={t("recordTasks.feedback")}
          />
          <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={onComplete}>
            <Glyph name="check" />
            {t("recordTasks.markDone")}
          </Button>
        </div>
      ) : null}
    </article>
  );
}

/** Overdue and due-today are derived from the due date, never stored. */
function dueLabel(task: RecordTaskRow, t: ReturnType<typeof useProjectsT>): string {
  if (task.status === "DROPPED") return t("recordTasks.dropped");
  if (task.status !== "OPEN" || !task.due_date) return "";
  const today = formatDateStorage(new Date()) ?? "";
  if (task.due_date < today) return t("recordTasks.overdue");
  if (task.due_date === today) return t("recordTasks.today");
  return "";
}

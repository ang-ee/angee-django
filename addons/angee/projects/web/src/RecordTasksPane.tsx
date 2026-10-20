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
  PaneRow,
  PaneRows,
  PaneToolbar,
  ScopeChip,
  SegmentedControl,
  Select,
  StatusIcon,
  Textarea,
  actionOutcomeSubmitResult,
  dateFromValue,
  formatDate,
  formatDateStorage,
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

/** The Tasks tab: the record's open or done tasks as pane rows under a scope
 * toolbar, a row's note and completion note when opened, then the scheduler and
 * the log for exchanges that already happened. The task schema derives access
 * from the record; the generic task verbs complete and drop, and a completion
 * posts on the record. */
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
  const [scope, setScope] = React.useState<"open" | "done">("open");
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  const textRef = React.useRef<HTMLInputElement | null>(null);
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
  const textField = form.form.register("text");
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
  React.useEffect(() => {
    if (expandedId && !tasks.some((task) => task.id === expandedId)) setExpandedId(null);
  }, [expandedId, tasks]);

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
  const shown = scope === "done" ? closed : open;
  const row = (task: RecordTaskRow) => {
    const kind = kindByKey.get(task.activity_type ?? "");
    const taskOpen = isOpenTask(task);
    const offers = (action: string) => taskOpen && offersTaskAction(task, action);
    const assignee = task.assignee ? task.assignee.display_name || task.assignee.username : "";
    const expanded = expandedId === task.id;
    return (
      <li key={task.id}>
        <PaneRow
          leading={offers("complete") ? (
            <button
              type="button"
              aria-label={t("recordTasks.markDone")}
              disabled={busy}
              onClick={(event) => { event.stopPropagation(); void handleComplete(task.id); }}
              className="grid size-4 place-items-center rounded-4 border border-border-strong text-fg-muted hover:bg-canvas hover:text-fg disabled:opacity-50"
            >
              <Glyph name="check" className="opacity-0 hover:opacity-100" />
            </button>
          ) : (
            <StatusIcon tone={taskOpen ? "muted" : "success"} icon={taskOpen ? kind?.glyph || "project-task" : "circle-check"} size="sm" />
          )}
          title={task.title}
          detail={kind?.name}
          meta={task.due_date ? formatDate(task.due_date) : undefined}
          sub={[assignee, dueLabel(task, t)].filter(Boolean).join(" · ")}
          verb={offers("drop") ? (
            <span role="button" aria-label={t("recordTasks.cancel")} onClick={(event) => { event.stopPropagation(); void handleCancel(task.id); }}>
              {t("recordTasks.cancel")}
            </span>
          ) : undefined}
          selected={expanded}
          onOpen={() => setExpandedId(expanded ? null : task.id)}
        />
        {expanded ? (
          <div className="ml-6 space-y-2 pb-2 pr-1">
            {task.note ? <p className="whitespace-pre-wrap text-13 text-fg-muted">{task.note}</p> : null}
            {offers("complete") ? (
              <Textarea
                value={feedbackById[task.id] ?? ""}
                onChange={(event) => setFeedbackById((current) => ({ ...current, [task.id]: event.currentTarget.value }))}
                rows={2}
                resize="none"
                aria-label={t("recordTasks.feedback")}
                placeholder={t("recordTasks.feedback")}
              />
            ) : null}
          </div>
        ) : null}
      </li>
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PaneToolbar
        scope={<>
          <ScopeChip label={t("recordTasks.open")} count={open.length} active={scope === "open"} onClick={() => setScope("open")} />
          <ScopeChip label={t("recordTasks.closed")} count={closed.length} active={scope === "done"} onClick={() => setScope("done")} />
        </>}
        actions={[{ icon: "calendar", label: t("recordTasks.schedule"), onClick: () => { setMode("schedule"); textRef.current?.focus(); } }]}
      />
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1">
        {shown.length > 0 ? (
          <section aria-label={scope === "done" ? t("recordTasks.closed") : t("recordTasks.open")}>
            <PaneRows>{shown.map(row)}</PaneRows>
          </section>
        ) : (
          <EmptyState
            icon="project-task"
            title={t("recordTasks.emptyTitle")}
            description={t("recordTasks.emptyHint")}
            className="min-h-40 p-4"
          />
        )}
      </div>
      <form onSubmit={handleSubmit} className="space-y-2 border-t border-border-subtle p-3">
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
            {...textField}
            ref={(element: HTMLInputElement | null) => { textField.ref(element); textRef.current = element; }}
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
          <p role="alert" className="text-12 text-danger-text">
            {(dayError ?? typeError)?.message}
          </p>
        ) : null}
        {form.formError ? <ErrorBanner description={form.formError} /> : null}
      </form>
    </div>
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

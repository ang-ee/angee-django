import { extractActionOutcome, useAuthoredMutation, useAuthoredQuery, type ActionOutcome } from "@angee/refine";
import * as React from "react";
import {
  Button,
  Checkbox,
  DatePopover,
  EmptyState,
  ErrorBanner,
  FieldRoot,
  FormRoot,
  Glyph,
  PaneLabelAction,
  PaneRow,
  PaneRows,
  PaneRowsSkeleton,
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
  textRoleVariants,
  useActionForm,
  useActionOutcomeMutation,
  useActionResultRun,
  useConfirm,
  useController,
  useUiT,
  type FormSubmitResult,
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

/**
 * The fields the form can show, in its order: a refusal's reason under one it draws shows at its
 * control. It draws the kind only when the catalog lists one (`kindShown`).
 */
const SHOWN_FIELDS: readonly (keyof TaskFormValues)[] = ["text", "note", "activityType", "day"];

/**
 * Each verb's name for a form field it does not take under the field's own name: the submit step
 * sends the field under it, and a refusal's reasons under it come back to the field. The text is
 * the schedule's title and the log's note. The log's note carries the form's note after the text,
 * so the server's one `note` key always lands on the text and the form's note is never marked there.
 */
const VERB_NAMES = {
  schedule: { text: "title", day: "dueDate" },
  log: {
    text: "note",
    // The refusal's key, not the argument: the log takes the day as `occurredOn` and refuses it
    // under `dueDate`, so its submit step sends `occurredOn` itself. When the verb refuses under
    // its own argument, this line reads `occurredOn` and the submit step reads it here.
    day: "dueDate",
  },
} as const satisfies Record<Mode, Partial<Record<keyof TaskFormValues, string>>>;

/** A refusal's reasons moved from the verb's `names` for the fields to the fields; other keys stay. */
function reasonsOnFields(
  result: FormSubmitResult<ActionOutcome>,
  names: Readonly<Partial<Record<keyof TaskFormValues, string>>>,
): FormSubmitResult<ActionOutcome> {
  if (result.status !== "invalid") return result;
  const fieldOf = new Map(Object.entries(names).map(([field, name]) => [name, field]));
  const fieldErrors: Record<string, readonly string[]> = {};
  for (const [name, reasons] of Object.entries(result.issues.fieldErrors)) {
    const field = fieldOf.get(name) ?? name;
    fieldErrors[field] = [...(fieldErrors[field] ?? []), ...reasons];
  }
  return { ...result, issues: { ...result.issues, fieldErrors } };
}

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
  const confirm = useConfirm();
  const [mode, setMode] = React.useState<Mode>("schedule");
  const [dateOpen, setDateOpen] = React.useState(false);
  const [feedbackById, setFeedbackById] = React.useState<Record<string, string>>({});
  const [scope, setScope] = React.useState<"open" | "done">("open");
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  const textRef = React.useRef<HTMLInputElement | null>(null);
  const kinds = tasksQuery.data?.activity_types ?? [];
  // The kind is drawn, claimed and checked only when the catalog lists one to choose; with none,
  // a reason under it shows on the banner and the server's answer decides a log's kind.
  const kindShown = kinds.length > 0;
  const form = useActionForm<TaskFormValues>({
    defaultValues: EMPTY_FORM,
    resolver: (values): ResolverResult<TaskFormValues> => {
      const text = values.text.trim();
      const errors: Record<string, { type: string; message: string }> = {};
      if (!text) errors.text = { type: "required", message: uiT("form.required") };
      if (mode === "log" && !values.day) errors.day = { type: "required", message: uiT("form.required") };
      if (mode === "log" && kindShown && !values.activityType) {
        errors.activityType = { type: "required", message: uiT("form.required") };
      }
      return Object.keys(errors).length ? { values: {}, errors } : { values: { ...values, text }, errors: {} };
    },
    shownFieldNames: kindShown ? SHOWN_FIELDS : SHOWN_FIELDS.filter((field) => field !== "activityType"),
    submit: async ({ text, note, day, activityType }, options) => {
      // A kind the pane does not draw is not sent: one chosen before the catalog emptied is still
      // among the form's values.
      const kind = kindShown ? activityType : "";
      if (mode === "log") {
        const names = VERB_NAMES.log;
        const answer = await logTask({
          ...variables, activityType: kind, [names.text]: note ? `${text}\n${note}` : text,
          // Not `names.day`, the key the log refuses the day under (see `VERB_NAMES`).
          occurredOn: day,
        }, options);
        return reasonsOnFields(actionOutcomeSubmitResult(extractActionOutcome(answer, "log_record_task")), names);
      }
      const names = VERB_NAMES.schedule;
      const answer = await scheduleTask({
        ...variables, [names.text]: text, note, [names.day]: day || null, activityType: kind || null,
      }, options);
      return reasonsOnFields(actionOutcomeSubmitResult(extractActionOutcome(answer, "schedule_record_task")), names);
    },
    onSuccess: () => {
      form.form.reset(EMPTY_FORM);
    },
    toastSuccess: false,
    genericErrorMessage: mode === "log" ? t("recordTasks.errorLog") : t("recordTasks.errorSchedule"),
  });
  // Each field hands the form its control, registered in the form's order: the form library focuses
  // a field its own check refuses, and a refusal's focus reaches it through `setFocus`.
  const textField = form.form.register("text");
  const noteField = form.form.register("note");
  const { field: typeField, fieldState: { error: typeError } } = useController({ control: form.form.control, name: "activityType" });
  const { field: dayField, fieldState: { error: dayError } } = useController({ control: form.form.control, name: "day" });
  const day = dayField.value;
  const textError = form.form.formState.errors.text;
  const noteError = form.form.formState.errors.note;
  const errorIds = React.useId();
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
    return <EmptyState icon="project-task" title={t("recordTasks.noRecord")} description={t("recordTasks.noRecordHint")} />;
  }
  if (tasksQuery.isFetching && tasksQuery.data === undefined) {
    return <PaneRowsSkeleton className="px-2 py-1" loadingLabel={t("recordTasks.loading")} />;
  }
  if (tasksQuery.error) {
    return <ErrorBanner title={t("recordTasks.unavailable")} description={t("recordTasks.unavailableHint")} className="m-3" />;
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    await form.run();
  }

  async function handleComplete(id: string): Promise<void> {
    const outcome = await settle(() => completeTask(id, { feedback: feedbackById[id] ?? "" }));
    if (outcome?.ok) setFeedbackById(({ [id]: _removed, ...rest }) => rest);
  }

  async function handleCancel(task: RecordTaskRow): Promise<void> {
    // Dropping is destructive: the shared confirm ceremony names the task first.
    if (!await confirm({ title: t("recordTasks.cancelTitle", { title: task.title }), body: t("recordTasks.cancelBody"), confirm: t("recordTasks.cancel"), danger: true })) return;
    await settle(() => dropTask(task.id, { reason: "OBSOLETE" }));
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
            <Checkbox
              size="sm"
              aria-label={t("recordTasks.markDone")}
              disabled={busy}
              checked={false}
              onCheckedChange={(checked) => { if (checked) void handleComplete(task.id); }}
            />
          ) : (
            <StatusIcon tone={taskOpen ? "muted" : "success"} icon={taskOpen ? kind?.glyph || "project-task" : "circle-check"} size="sm" />
          )}
          title={task.title}
          detail={kind?.name}
          meta={task.due_date ? formatDate(task.due_date) : undefined}
          sub={[assignee, dueLabel(task, t)].filter(Boolean).join(" · ")}
          verb={offers("drop") ? (
            <PaneLabelAction icon="circle-x" disabled={busy} onClick={() => void handleCancel(task)}>
              {t("recordTasks.cancel")}
            </PaneLabelAction>
          ) : undefined}
          selected={expanded}
          onOpen={() => setExpandedId(expanded ? null : task.id)}
        />
        {expanded ? (
          <div className="ml-6 space-y-2 pb-2 pr-1">
            {task.note ? <p className={textRoleVariants({ role: "meta", className: "whitespace-pre-wrap" })}>{task.note}</p> : null}
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
          <EmptyState icon="project-task" title={t("recordTasks.emptyTitle")} description={t("recordTasks.emptyHint")} />
        )}
      </div>
      <FormRoot onSubmit={handleSubmit} className="space-y-2 border-t border-border-subtle p-3">
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
        {/* The field marks the text input and the kind's select and ties their reason itself; the
            note's textarea and the day's popover trigger are not its controls, so they carry both by hand. */}
        <FieldRoot invalid={Boolean(noteError)}>
          <Textarea
            {...noteField}
            rows={2}
            resize="none"
            aria-label={t("recordTasks.note")}
            placeholder={t("recordTasks.note")}
            aria-invalid={noteError ? true : undefined}
            aria-describedby={noteError ? `${errorIds}-note` : undefined}
          />
          {noteError ? <FieldRoot.Error id={`${errorIds}-note`} match>{noteError.message}</FieldRoot.Error> : null}
        </FieldRoot>
        <div className="flex items-start gap-2">
          {kindShown ? (
            // It shares the row with the day: at the row's whole width the day's button had none left.
            <FieldRoot invalid={Boolean(typeError)} className="min-w-0 flex-1">
              <Select
                triggerRef={typeField.ref}
                aria-label={t("recordTasks.type")}
                placeholder={t("recordTasks.type")}
                options={kindOptions}
                value={typeField.value}
                onValueChange={(value) => typeField.onChange(value)}
                // Its border: the select draws it from its own prop, not from the field.
                invalid={Boolean(typeError)}
              />
              {typeError ? <FieldRoot.Error match>{typeError.message}</FieldRoot.Error> : null}
            </FieldRoot>
          ) : null}
          <FieldRoot invalid={Boolean(dayError)} className="min-w-0 flex-1">
            <DatePopover
              triggerRef={dayField.ref}
              controlProps={{
                id: `${errorIds}-day-control`,
                ...(dayError ? { "aria-invalid": true, "aria-describedby": `${errorIds}-day` } : {}),
              }}
              selected={dateFromValue(day)}
              label={dayLabel}
              ariaLabel={mode === "log" ? t("recordTasks.occurredOn") : t("recordTasks.dueDate")}
              open={dateOpen}
              onOpenChange={setDateOpen}
              onSelectDate={(date) => dayField.onChange(formatDateStorage(date) ?? "")}
              footer={
                day ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="mt-2 w-full"
                    onClick={() => {
                      dayField.onChange("");
                      setDateOpen(false);
                    }}
                  >
                    <Glyph name="x" />
                    {t("recordTasks.clearDate")}
                  </Button>
                ) : null
              }
            />
            {dayError ? <FieldRoot.Error id={`${errorIds}-day`} match>{dayError.message}</FieldRoot.Error> : null}
          </FieldRoot>
          <Button type="submit" variant="primary" size="sm" disabled={form.submitting}>
            <Glyph name={mode === "log" ? "check" : "calendar"} />
            {mode === "log" ? t("recordTasks.log") : t("recordTasks.schedule")}
          </Button>
        </div>
        <ErrorBanner {...form.banner} messages={form.formErrors} />
      </FormRoot>
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

import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  Action,
  canonicalOptionValue,
  Field,
  Form,
  Group,
  defineRowAction,
  useActionResultMutation,
  useActionOutcomeMutation,
  useEnumOptions,
  useActionResultRun,
  useRecordAction,
  useRecordActionMutation,
  type ActionDescriptor,
  type RowActionDeclaration,
  type StringIdRow,
  type WidgetOption,
  type FormProps,
} from "@angee/ui";
import * as React from "react";

import { SetTaskVisibilityDocument } from "./documents";
import { useProjectsT } from "./i18n";
import { PROJECT_MODEL, TASK_MODEL } from "./resources";

export interface TaskActionRow extends StringIdRow {
  status?: unknown;
}

/** Task lifecycle verbs shared by every task collection surface. */
export function useTaskRowActions<
  TRow extends TaskActionRow,
>(): readonly RowActionDeclaration<TRow>[] {
  const t = useProjectsT();
  const [complete] = useActionResultMutation<ActionFieldName>("complete_task", {
    invalidateModels: [TASK_MODEL],
  });
  const [reopen] = useActionResultMutation<ActionFieldName>("reopen_task", {
    invalidateModels: [TASK_MODEL],
  });

  return React.useMemo(
    () => [
      defineRowAction<TRow>({
        kind: "page",
        id: "complete-task",
        label: t("task.action.complete"),
        icon: "check",
        variant: "ghost",
        visible: (row) => taskStatus(row) === "open",
        pendingPolicy: "active-row",
        onSelect: (row) => complete(row.id),
      }),
      defineRowAction<TRow>({
        kind: "page",
        id: "reopen-task",
        label: t("task.action.reopen"),
        icon: "activity",
        variant: "ghost",
        visible: (row) => taskStatus(row) !== "open",
        pendingPolicy: "active-row",
        onSelect: (row) => reopen(row.id),
      }),
    ],
    [complete, reopen, t],
  );
}

export interface TaskFormSelection {
  groups?: readonly ("placement" | "assignment" | "details")[];
  verbs?: readonly ("complete" | "drop" | "reopen" | "promote")[];
  contextLine?: FormProps["contextLine"];
  returning?: FormProps["returning"];
  /**
   * The statusbar: the task's own `status` (default), or another owner's field
   * rendered by that owner's widget (e.g. a queue stage contributed by work).
   */
  statusField?: "status" | { name: string; widget: string };
  /** The visibility control beside the title; a host that states audiences per section drops it. */
  visibility?: boolean;
  extraFields?: React.ReactNode;
  extraActions?: React.ReactNode;
}

/** Shared task form for collection routes and board create flows. */
export function useTaskFormDeclaration(selection: TaskFormSelection = {}): React.ReactElement {
  const t = useProjectsT();
  const visibilityOptions = useEnumOptions(TASK_MODEL, "visibility", { casing: "upper" });
  const statusOptions = useEnumOptions(TASK_MODEL, "status");
  const priorityOptions = useEnumOptions(TASK_MODEL, "priority");
  const dropReasonOptions = useEnumOptions(TASK_MODEL, "dropped_reason", { casing: "upper" });
  const [complete] = useRecordActionMutation<ActionFieldName>("complete_task", {
    invalidateModels: [TASK_MODEL],
    settle: true,
  });
  const [reopen] = useRecordActionMutation<ActionFieldName>("reopen_task", {
    invalidateModels: [TASK_MODEL],
    settle: true,
  });
  const [promoteTask] = useActionOutcomeMutation<ActionFieldName>("promote_task_to_project", {
    invalidateModels: [TASK_MODEL, PROJECT_MODEL],
  });
  const settlePromotion = useActionResultRun({ linkTo: PROJECT_MODEL });
  const promote = useRecordAction(async (id, context) => {
    await settlePromotion(() => promoteTask(id, { expected_revision: context.record?.revision }));
  });
  const [dropTask] = useActionOutcomeMutation<ActionFieldName>("drop_task", {
    invalidateModels: [TASK_MODEL],
  });
  const dropSubmit = React.useCallback<
    NonNullable<ActionDescriptor["submit"]>
  >(
    async (values, context) => {
      const id = context.record?.id;
      if (typeof id !== "string" || id === "") {
        return { ok: false, message: t("task.action.failed") };
      }
      return (await dropTask(id, {
        reason: dropReason(dropReasonOptions, values.reason),
      })) ?? { ok: false, message: t("task.action.failed") };
    },
    [dropReasonOptions, dropTask, t],
  );

  const standardActions = <>
    {(selection.verbs ?? ["complete", "drop", "reopen", "promote"]).includes("complete") ? <Action
      id="complete" placement="toolbar" label={t("task.action.complete")} permission="write"
      icon="check" run={complete} visibleWhen={isOpenTask} /> : null}
    {(selection.verbs ?? ["complete", "drop", "reopen", "promote"]).includes("drop") ? <Action
      id="drop" label={t("task.action.drop")} permission="write" icon="circle-x" danger
      args={[{ name: "reason", label: t("task.action.reason"), widget: "select", options: dropReasonOptions }]}
      submit={dropSubmit} visibleWhen={isOpenTask} /> : null}
    {(selection.verbs ?? ["complete", "drop", "reopen", "promote"]).includes("reopen") ? <Action
      id="reopen" label={t("task.action.reopen")} permission="write"
      icon="activity" run={reopen} visibleWhen={(record) => !isOpenTask(record)} /> : null}
    {(selection.verbs ?? ["complete", "drop", "reopen", "promote"]).includes("promote") ? <Action
      id="promote" label={t("task.action.promote")} permission="write"
      icon="projects" run={promote} /> : null}
  </>;
  return (
    <Form resource={TASK_MODEL} layout="tabs" contextLine={selection.contextLine} returning={selection.returning}>
      <Field name="title" title />
      <Field name="allowed_visibility" hidden readOnly />
      <Field name="audience_label" hidden readOnly />
      <Field name="revision" readOnly hidden />
      {selection.visibility === false ? null
        : <Field name="visibility" widget="visibility" placement="title" options={visibilityOptions}
          visibilityAction={{ document: SetTaskVisibilityDocument, resultField: "set_task_visibility",
            idArgument: "id", revisionArgument: "expected_revision", audienceField: "audience_label" }} />}
      {selection.statusField && selection.statusField !== "status"
        ? <Field name={selection.statusField.name} widget={selection.statusField.widget} status readOnly />
        : <Field name="status" widget="statusbar" status options={statusOptions} createOnly />}
      {(selection.groups ?? ["placement", "assignment", "details"]).includes("placement") ? <Group label={t("task.group.placement")} columns={2}>
        <Field name="project" />
        <Field name="milestone" />
        <Field name="parent" />
      </Group> : null}
      {(selection.groups ?? ["placement", "assignment", "details"]).includes("assignment") ? <Group label={t("task.group.assignment")} columns={2}>
        <Field name="assignee" />
        <Field name="delegate" />
        <Field name="priority" options={priorityOptions} />
        <Field name="due_date" />
        <Field name="recurrence" />
      </Group> : null}
      {(selection.groups ?? ["placement", "assignment", "details"]).includes("details") ? <Group label={t("task.group.details")} columns={2} collapsible defaultOpen={false}>
        <Field name="sort_order" label={t("common.order")} createOnly />
        <Field name="sub_sort_order" label={t("common.subtaskOrder")} createOnly />
        <Field name="dropped_reason" readOnly />
        <Field name="done_at" readOnly />
        <Field name="dropped_at" readOnly />
      </Group> : null}
      <Field name="note" widget="markdown.editor" body />
      {selection.extraFields}
      {standardActions}
      {selection.extraActions}
    </Form>
  );
}

function isOpenTask(record: { status?: unknown }): boolean {
  return taskStatus(record) === "open";
}

function taskStatus(record: { status?: unknown }): string {
  return String(record.status ?? "").trim().toLowerCase();
}

export function dropReason(options: readonly WidgetOption[], value: unknown): string {
  const reason = canonicalOptionValue(options, value);
  if (reason !== undefined) return reason;
  throw new TypeError("Task drop reason declaration produced an invalid enum value.");
}

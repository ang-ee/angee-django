import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  extractActionOutcome,
  type DocumentVariables,
} from "@angee/refine";
import {
  Action,
  Field,
  Form,
  Group,
  defineRowAction,
  useActionResultMutation,
  useAuthoredResourceMutation,
  useEnumOptions,
  useRecordActionMutation,
  type ActionDescriptor,
  type RowActionDeclaration,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { DropTaskDocument } from "./documents";
import { useProjectsT } from "./i18n";
import { PROJECT_MODEL, TASK_MODEL } from "./resources";

export interface TaskActionRow extends StringIdRow {
  status?: unknown;
}

type DropTaskVariables = DocumentVariables<typeof DropTaskDocument>;

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

/** One Form declaration reused by the routed task page and personal board create flow. */
export function useTaskFormDeclaration(): React.ReactElement {
  const t = useProjectsT();
  const statusOptions = useEnumOptions(TASK_MODEL, "status");
  const priorityOptions = useEnumOptions(TASK_MODEL, "priority");
  const dropReasonOptions = useEnumOptions(TASK_MODEL, "dropped_reason").map(
    (option) => ({ ...option, value: String(option.value).toUpperCase() }),
  );
  const [complete] = useRecordActionMutation<ActionFieldName>("complete_task", {
    invalidateModels: [TASK_MODEL],
    settle: true,
  });
  const [reopen] = useRecordActionMutation<ActionFieldName>("reopen_task", {
    invalidateModels: [TASK_MODEL],
    settle: true,
  });
  const [promote] = useRecordActionMutation<ActionFieldName>(
    "promote_task_to_project",
    {
      invalidateModels: [TASK_MODEL, PROJECT_MODEL],
      linkTo: PROJECT_MODEL,
      settle: true,
    },
  );
  const [dropTask] = useAuthoredResourceMutation(DropTaskDocument, {
    invalidateModels: [TASK_MODEL],
    shouldInvalidate: (data) => data?.drop_task.ok === true,
  });
  const dropSubmit = React.useCallback<
    NonNullable<ActionDescriptor["submit"]>
  >(
    async (values, context) => {
      const id = context.record?.id;
      if (typeof id !== "string" || id === "") {
        return { ok: false, message: t("task.action.failed") };
      }
      const data = await dropTask({
        id,
        reason: dropReason(values.reason),
      });
      return (
        extractActionOutcome(data, "drop_task") ?? {
          ok: false,
          message: t("task.action.failed"),
        }
      );
    },
    [dropTask, t],
  );

  return (
    <Form resource={TASK_MODEL} layout="tabs">
      <Field name="title" title />
      <Field name="status" widget="statusbar" options={statusOptions} createOnly />
      <Group label={t("task.group.placement")} columns={2}>
        <Field name="project" />
        <Field name="milestone" />
        <Field name="parent" />
      </Group>
      <Group label={t("task.group.assignment")} columns={2}>
        <Field name="assignee" />
        <Field name="delegate" />
        <Field name="priority" options={priorityOptions} />
        <Field name="due_date" />
        <Field name="recurrence" />
      </Group>
      <Group label={t("task.group.ordering")} columns={2}>
        <Field name="sort_order" label={t("common.order")} createOnly />
        <Field
          name="sub_sort_order"
          label={t("common.subtaskOrder")}
          createOnly
        />
        <Field name="dropped_reason" readOnly />
        <Field name="done_at" readOnly />
        <Field name="dropped_at" readOnly />
      </Group>
      <Field name="note" widget="markdown.editor" body />
      <Action
        id="complete"
        label={t("task.action.complete")}
        icon="check"
        run={complete}
        visibleWhen={isOpenTask}
      />
      <Action
        id="drop"
        label={t("task.action.drop")}
        icon="circle-x"
        danger
        args={[
          {
            name: "reason",
            label: t("task.action.reason"),
            widget: "select",
            options: dropReasonOptions,
          },
        ]}
        submit={dropSubmit}
        visibleWhen={isOpenTask}
      />
      <Action
        id="reopen"
        label={t("task.action.reopen")}
        icon="activity"
        run={reopen}
        visibleWhen={(record) => !isOpenTask(record)}
      />
      <Action
        id="promote"
        label={t("task.action.promote")}
        icon="projects"
        run={promote}
        visibleWhen={() => true}
      />
    </Form>
  );
}

function isOpenTask(record: { status?: unknown }): boolean {
  return taskStatus(record) === "open";
}

function taskStatus(record: { status?: unknown }): string {
  return String(record.status ?? "").trim().toLowerCase();
}

function dropReason(value: unknown): DropTaskVariables["reason"] {
  if (value === "DUPLICATE" || value === "DECLINED" || value === "OBSOLETE") {
    return value;
  }
  throw new TypeError("Task drop reason declaration produced an invalid enum value.");
}

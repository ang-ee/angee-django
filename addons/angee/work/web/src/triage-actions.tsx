import type { ActionFieldName } from "@angee/gql/console/actions";
import type { TaskDroppedReason } from "@angee/gql/console/graphql";
import { extractActionOutcome, type DocumentVariables } from "@angee/refine";
import {
  RecordActionBar,
  canonicalOptionValue,
  relationValueId,
  useActionOutcomeMutation,
  useAuthoredResourceMutation,
  useRecordChromeContext,
  useRecordChromeActionMutation,
  useDescriptorRowActions,
  type ActionDescriptor,
  type RowActionDeclaration,
  type WidgetOption,
} from "@angee/ui";
import * as React from "react";
import { TASK_MODEL, offersTaskAction } from "@angee/projects";

import { AcceptTaskDocument, DeclineTaskDocument } from "./documents";
import { useTaskContext } from "./context";
import { useWorkT } from "./i18n";
import { STAGE_MODEL } from "./resources";
import { acceptStageFilters } from "./stage-filters";
import { isTaskInTriage, type WorkTaskRow } from "./task-work";

type AcceptTaskVariables = DocumentVariables<typeof AcceptTaskDocument>;

/** The four authored triage verbs, with queue-safe relation pickers. */
export function useTriageActions(queueId: string): readonly ActionDescriptor[] {
  const t = useWorkT();
  const declineReasonOptions = React.useMemo<readonly WidgetOption[]>(
    () => [
      { value: "DECLINED", label: t("triage.reason.declined") },
      { value: "OBSOLETE", label: t("triage.reason.obsolete") },
    ],
    [t],
  );
  const [accept] = useAuthoredResourceMutation(AcceptTaskDocument, {
    invalidateModels: [TASK_MODEL],
    shouldInvalidate: (data) => data?.accept_task.ok === true,
  });
  const [decline] = useAuthoredResourceMutation(DeclineTaskDocument, {
    invalidateModels: [TASK_MODEL],
    shouldInvalidate: (data) => data?.decline_task.ok === true,
  });
  const [snooze] = useActionOutcomeMutation<ActionFieldName>("snooze_task", {
    idArgument: "task",
    invalidateModels: [TASK_MODEL],
  });
  const [duplicate] = useActionOutcomeMutation<ActionFieldName>("mark_task_duplicate", {
    idArgument: "task",
    invalidateModels: [TASK_MODEL],
  });

  return React.useMemo(
    () => [
      {
        id: "work-accept-task",
        label: t("triage.action.accept"),
        icon: "work-accept",
        args: [
          {
            name: "stage",
            label: t("triage.action.stage"),
            argKind: "relation" as const,
            resource: STAGE_MODEL,
            filters: acceptStageFilters(queueId),
          },
        ],
        submit: async (values, context) => {
          const task = recordId(context.record, t("triage.action.failed"));
          const data = await accept({
            task,
            stage: requiredString(values.stage, "stage"),
          } satisfies AcceptTaskVariables);
          return extractActionOutcome(data, "accept_task") ?? {
            ok: false,
            message: t("triage.action.failed"),
          };
        },
      },
      {
        id: "work-decline-task",
        label: t("triage.action.decline"),
        icon: "work-decline",
        danger: true,
        args: [
          {
            name: "reason",
            label: t("triage.action.reason"),
            widget: "select" as const,
            options: declineReasonOptions,
          },
        ],
        submit: async (values, context) => {
          const task = recordId(context.record, t("triage.action.failed"));
          return extractActionOutcome(await decline({ task,
            reason: declineReason(declineReasonOptions, values.reason),
          }), "decline_task") ?? { ok: false, message: t("triage.action.failed") };
        },
      },
      {
        id: "work-snooze-task",
        label: t("triage.action.snooze"),
        icon: "work-snooze",
        args: [
          {
            name: "until",
            label: t("triage.action.until"),
            kind: "datetime" as const,
          },
        ],
        submit: async (values, context) => {
          const task = recordId(context.record, t("triage.action.failed"));
          return (await snooze(task, {
            until: requiredString(values.until, "until"),
          })) ?? { ok: false, message: t("triage.action.failed") };
        },
      },
      {
        id: "work-duplicate-task",
        label: t("triage.action.duplicate"),
        icon: "work-duplicate",
        danger: true,
        args: [
          {
            name: "canonical",
            label: t("triage.action.canonical"),
            argKind: "relation" as const,
            resource: TASK_MODEL,
            filters: canonicalTaskFilters(queueId),
          },
        ],
        submit: async (values, context) => {
          const task = recordId(context.record, t("triage.action.failed"));
          return (await duplicate(task, {
            canonical: requiredString(values.canonical, "canonical"),
          })) ?? { ok: false, message: t("triage.action.failed") };
        },
      },
    ],
    [accept, decline, declineReasonOptions, duplicate, queueId, snooze, t],
  );
}

/** Row-action declarations plus the one dialog owner for a triage list. */
export function useTriageRowActions<TRow extends WorkTaskRow>(queueId: string): {
  rowActions: readonly RowActionDeclaration<TRow>[];
  dialog: React.ReactNode;
} {
  const actions = useTriageActions(queueId);
  return useDescriptorRowActions<TRow>(actions, { visible: (_action, row) => isTaskInTriage(row) });
}

/** Projects' routed task FormView record-toolbar contribution. */
export function TriageRecordActions(): React.ReactElement | null {
  const t = useWorkT();
  const context = useRecordChromeContext();
  const record = context.record as WorkTaskRow | null;
  const queueId = relationValueId(record?.queue);
  const actions = useTriageActions(queueId);
  const taskContext = useTaskContext(context.recordId);
  const stage = taskContext.data?.project_tasks_by_pk?.stage ?? record?.stage;
  const [start, startState] = useRecordChromeActionMutation<ActionFieldName>("start_task");
  const [returnToTriage, returnState] = useRecordChromeActionMutation<ActionFieldName>("return_task_to_triage");
  if (!record || !queueId || context.formReadOnly) return null;
  if (!isTaskInTriage(record)) {
    // Start shows where it would act (a waiting stage); the row's task_actions admit both verbs.
    const verbs: ActionDescriptor[] = [];
    if (["BACKLOG", "UNSTARTED"].includes(String(stage?.category).toUpperCase()) && offersTaskAction(record, "start")) verbs.push({
      id: "work-start-task", label: t("task.action.start"), icon: "work-start",
      disabled: startState.fetching || returnState.fetching,
      run: () => start(context.recordId),
    });
    if (offersTaskAction(record, "return_to_triage")) verbs.push({
      id: "work-return-to-triage", label: t("triage.action.return"), icon: "work-triage",
      disabled: startState.fetching || returnState.fetching,
      run: () => returnToTriage(context.recordId),
    });
    return <RecordActionBar record={record} actions={verbs} />;
  }
  return <RecordActionBar record={record} actions={actions} />;
}

function canonicalTaskFilters(queueId: string) {
  // Static ActionRelationArg.filters cannot exclude the active row; mark_duplicate owns that clean error.
  return [
    { field: "queue", operator: "eq" as const, value: queueId },
    { field: "status", operator: "ne" as const, value: "DROPPED" },
  ];
}

function recordId(record: Record<string, unknown> | null, message: string): string {
  const value = record?.id;
  if (typeof value === "string" && value) return value;
  throw new TypeError(message);
}

function requiredString(value: unknown, name: string): string {
  if (typeof value === "string" && value.trim()) return value;
  throw new TypeError(`${name} is required.`);
}

export function declineReason(options: readonly WidgetOption[], value: unknown): TaskDroppedReason {
  const reason = canonicalOptionValue(options, value);
  if (reason !== undefined) return reason as TaskDroppedReason;
  throw new TypeError("A decline reason is required.");
}

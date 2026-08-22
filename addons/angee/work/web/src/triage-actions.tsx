import {
  extractActionOutcome,
  type DocumentVariables,
} from "@angee/refine";
import {
  ActionFormDialog,
  Button,
  Glyph,
  defineRowAction,
  relationValueId,
  useAuthoredResourceMutation,
  useRecordChromeContext,
  type ActionDescriptor,
  type RowActionDeclaration,
} from "@angee/ui";
import * as React from "react";

import {
  AcceptTaskDocument,
  DeclineTaskDocument,
  MarkTaskDuplicateDocument,
  SnoozeTaskDocument,
} from "./documents";
import { useWorkT } from "./i18n";
import { STAGE_MODEL } from "./resources";
import { queueStageFilters } from "./stage-filters";
import { isTaskInTriage, type WorkTaskRow } from "./task-work";

const TASK_MODEL = "projects.Task";

type AcceptVariables = DocumentVariables<typeof AcceptTaskDocument>;
type DeclineVariables = DocumentVariables<typeof DeclineTaskDocument>;
type SnoozeVariables = DocumentVariables<typeof SnoozeTaskDocument>;
type DuplicateVariables = DocumentVariables<typeof MarkTaskDuplicateDocument>;

/** The four authored triage verbs, with queue-safe relation pickers. */
export function useTriageActions(queueId: string): readonly ActionDescriptor[] {
  const t = useWorkT();
  const [accept] = useAuthoredResourceMutation(AcceptTaskDocument, {
    invalidateModels: [TASK_MODEL],
    shouldInvalidate: (data) => data?.accept_task.ok === true,
  });
  const [decline] = useAuthoredResourceMutation(DeclineTaskDocument, {
    invalidateModels: [TASK_MODEL],
    shouldInvalidate: (data) => data?.decline_task.ok === true,
  });
  const [snooze] = useAuthoredResourceMutation(SnoozeTaskDocument, {
    invalidateModels: [TASK_MODEL],
    shouldInvalidate: (data) => data?.snooze_task.ok === true,
  });
  const [duplicate] = useAuthoredResourceMutation(MarkTaskDuplicateDocument, {
    invalidateModels: [TASK_MODEL],
    shouldInvalidate: (data) => data?.mark_task_duplicate.ok === true,
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
            filters: queueStageFilters(queueId),
          },
        ],
        submit: async (values, context) => {
          const data = await accept({
            task: recordId(context.record, t("triage.action.failed")),
            stage: requiredString(values.stage, "stage"),
          } satisfies AcceptVariables);
          return (
            extractActionOutcome(data, "accept_task") ?? {
              ok: false,
              message: t("triage.action.failed"),
            }
          );
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
            options: [
              { value: "DECLINED", label: t("triage.reason.declined") },
              { value: "OBSOLETE", label: t("triage.reason.obsolete") },
            ],
          },
        ],
        submit: async (values, context) => {
          const data = await decline({
            task: recordId(context.record, t("triage.action.failed")),
            reason: declineReason(values.reason),
          } satisfies DeclineVariables);
          return (
            extractActionOutcome(data, "decline_task") ?? {
              ok: false,
              message: t("triage.action.failed"),
            }
          );
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
          const data = await snooze({
            task: recordId(context.record, t("triage.action.failed")),
            until: requiredString(values.until, "until"),
          } satisfies SnoozeVariables);
          return (
            extractActionOutcome(data, "snooze_task") ?? {
              ok: false,
              message: t("triage.action.failed"),
            }
          );
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
          const data = await duplicate({
            task: recordId(context.record, t("triage.action.failed")),
            canonical: requiredString(values.canonical, "canonical"),
          } satisfies DuplicateVariables);
          return (
            extractActionOutcome(data, "mark_task_duplicate") ?? {
              ok: false,
              message: t("triage.action.failed"),
            }
          );
        },
      },
    ],
    [accept, decline, duplicate, queueId, snooze, t],
  );
}

/** Row-action declarations plus the one dialog owner for a triage list. */
export function useTriageRowActions<TRow extends WorkTaskRow>(queueId: string): {
  rowActions: readonly RowActionDeclaration<TRow>[];
  dialog: React.ReactNode;
} {
  const actions = useTriageActions(queueId);
  const [active, setActive] = React.useState<{
    action: ActionDescriptor;
    row: TRow;
  } | null>(null);
  const rowActions = React.useMemo(
    () =>
      actions.map((action) =>
        defineRowAction<TRow>({
          kind: "page",
          id: action.id,
          label: String(action.label),
          icon: action.icon,
          variant: action.danger ? "danger" : "ghost",
          visible: isTaskInTriage,
          pendingPolicy: "disable-actions",
          onSelect: (row) => setActive({ action, row }),
        }),
      ),
    [actions],
  );
  return {
    rowActions,
    dialog: active ? (
      <ActionFormDialog
        key={`${active.action.id}:${active.row.id}`}
        action={active.action}
        context={{ record: active.row, selectedIds: [active.row.id] }}
        open
        onOpenChange={(open) => {
          if (!open) setActive(null);
        }}
      />
    ) : null,
  };
}

/** Projects' routed task FormView record-toolbar contribution. */
export function TriageRecordActions(): React.ReactElement | null {
  const context = useRecordChromeContext();
  const record = context.record as WorkTaskRow | null;
  const queueId = relationValueId(record?.queue);
  const actions = useTriageActions(queueId);
  const [active, setActive] = React.useState<ActionDescriptor | null>(null);
  if (!record || !queueId || !isTaskInTriage(record)) return null;
  return (
    <>
      <div className="flex flex-wrap items-center justify-end gap-1">
        {actions.map((action) => (
          <Button
            key={action.id}
            type="button"
            size="sm"
            variant={action.danger ? "danger" : "ghost"}
            onClick={() => setActive(action)}
          >
            {action.icon ? <Glyph decorative name={action.icon} /> : null}
            {action.label}
          </Button>
        ))}
      </div>
      {active ? (
        <ActionFormDialog
          key={active.id}
          action={active}
          context={{ record, selectedIds: [context.recordId] }}
          open
          onOpenChange={(open) => {
            if (!open) setActive(null);
          }}
        />
      ) : null}
    </>
  );
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

function declineReason(value: unknown): DeclineVariables["reason"] {
  if (value === "DECLINED" || value === "OBSOLETE") return value;
  throw new TypeError("A decline reason is required.");
}

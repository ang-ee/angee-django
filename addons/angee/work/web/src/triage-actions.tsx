import type { ActionFieldName } from "@angee/gql/console/actions";
import { extractActionOutcome, type DocumentVariables } from "@angee/refine";
import {
  ActionFormDialog,
  Button,
  Glyph,
  canonicalOptionValue,
  defineRowAction,
  relationValueId,
  useActionOutcomeMutation,
  useAuthoredResourceMutation,
  useRecordChromeContext,
  type ActionDescriptor,
  type RowActionDeclaration,
  type WidgetOption,
} from "@angee/ui";
import * as React from "react";

import { AcceptTaskDocument } from "./documents";
import { useWorkT } from "./i18n";
import { STAGE_MODEL } from "./resources";
import { queueStageFilters } from "./stage-filters";
import { isTaskInTriage, type WorkTaskRow } from "./task-work";

const TASK_MODEL = "projects.Task";
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
  const [decline] = useActionOutcomeMutation<ActionFieldName>("decline_task", {
    idArgument: "task",
    invalidateModels: [TASK_MODEL],
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
            filters: queueStageFilters(queueId),
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
          return (await decline(task, {
            reason: declineReason(declineReasonOptions, values.reason),
          })) ?? { ok: false, message: t("triage.action.failed") };
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

export function declineReason(options: readonly WidgetOption[], value: unknown): string {
  const reason = canonicalOptionValue(options, value);
  if (reason !== undefined) return reason;
  throw new TypeError("A decline reason is required.");
}

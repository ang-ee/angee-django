import {
  extractActionOutcome,
  type DocumentVariables,
} from "@angee/refine";
import {
  ActionFormDialog,
  Button,
  Glyph,
  defineRowAction,
  useAuthoredResourceMutation,
  type ActionDescriptor,
  type RowActionDeclaration,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { CloseWorkCycleDocument } from "./documents";
import { useWorkT } from "./i18n";
import { CYCLE_MODEL } from "./resources";

type CloseCycleVariables = DocumentVariables<typeof CloseWorkCycleDocument>;

export interface WorkCycleRow extends StringIdRow {
  starts_on?: unknown;
  completed_at?: unknown;
}

/** Authored cycle close/rollover descriptor; in-band next_cycle errors stay open. */
export function useCloseCycleAction(): ActionDescriptor {
  const t = useWorkT();
  const [close] = useAuthoredResourceMutation(CloseWorkCycleDocument, {
    invalidateModels: [CYCLE_MODEL, "projects.Task"],
    shouldInvalidate: (data) => data?.close_work_cycle.ok === true,
  });
  return React.useMemo(
    () => ({
      id: "work-close-cycle",
      label: t("cycle.action.close"),
      icon: "work-cycle-close",
      args: [],
      submit: async (_values, context) => {
        const cycle = context.record?.id;
        if (typeof cycle !== "string" || !cycle) {
          return { ok: false, message: t("cycle.action.failed") };
        }
        const data = await close({ cycle } satisfies CloseCycleVariables);
        return (
          extractActionOutcome(data, "close_work_cycle") ?? {
            ok: false,
            message: t("cycle.action.failed"),
          }
        );
      },
    }),
    [close, t],
  );
}

export function useCycleRowActions<TRow extends WorkCycleRow>(): {
  rowActions: readonly RowActionDeclaration<TRow>[];
  dialog: React.ReactNode;
} {
  const action = useCloseCycleAction();
  const [active, setActive] = React.useState<TRow | null>(null);
  const rowActions = React.useMemo(
    () => [
      defineRowAction<TRow>({
        kind: "page",
        id: action.id,
        label: String(action.label),
        icon: action.icon,
        variant: "ghost",
        visible: canCloseCycle,
        pendingPolicy: "disable-actions",
        onSelect: setActive,
      }),
    ],
    [action],
  );
  return {
    rowActions,
    dialog: active ? (
      <ActionFormDialog
        key={active.id}
        action={action}
        context={{ record: active, selectedIds: [active.id] }}
        open
        onOpenChange={(open) => {
          if (!open) setActive(null);
        }}
      />
    ) : null,
  };
}

export function CycleCloseControl({
  cycle,
}: {
  cycle: WorkCycleRow;
}): React.ReactElement | null {
  const action = useCloseCycleAction();
  const [open, setOpen] = React.useState(false);
  if (!canCloseCycle(cycle)) return null;
  return (
    <>
      <Button type="button" size="sm" variant="secondary" onClick={() => setOpen(true)}>
        <Glyph decorative name="work-cycle-close" />
        {action.label}
      </Button>
      {open ? (
        <ActionFormDialog
          action={action}
          context={{ record: cycle, selectedIds: [cycle.id] }}
          open
          onOpenChange={setOpen}
        />
      ) : null}
    </>
  );
}

export function canCloseCycle(row: WorkCycleRow): boolean {
  if (row.completed_at != null) return false;
  if (typeof row.starts_on !== "string" || !row.starts_on) return true;
  return row.starts_on.slice(0, 10) <= new Date().toISOString().slice(0, 10);
}

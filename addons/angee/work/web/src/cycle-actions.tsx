import type { ActionFieldName } from "@angee/gql/console/actions";
import {
  ActionFormDialog,
  Button,
  Glyph,
  defineRowAction,
  useActionOutcomeMutation,
  type ActionDescriptor,
  type RowActionDeclaration,
  type StringIdRow,
} from "@angee/ui";
import * as React from "react";

import { useWorkT } from "./i18n";
import { CYCLE_MODEL } from "./resources";

export interface WorkCycleRow extends StringIdRow {
  starts_on?: unknown;
  completed_at?: unknown;
}

/** Authored cycle close/rollover descriptor; in-band next_cycle errors stay open. */
export function useCloseCycleAction(): ActionDescriptor {
  const t = useWorkT();
  const [close] = useActionOutcomeMutation<ActionFieldName>("close_work_cycle", {
    invalidateModels: [CYCLE_MODEL, "projects.Task"],
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
        return (await close(cycle, { cycle })) ?? {
          ok: false,
          message: t("cycle.action.failed"),
        };
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

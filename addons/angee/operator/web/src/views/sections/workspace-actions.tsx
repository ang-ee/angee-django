import { Button, useConfirm, useToast } from "@angee/base";
import { useMemo, type ReactNode } from "react";

import {
  WORKSPACE_DESTROY_MUTATION,
  WORKSPACE_SYNC_BASE_MUTATION,
} from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
import type { WorkspaceRef } from "../../data/types";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

interface WorkspaceActionVars extends Record<string, unknown> {
  name: string;
}

/** A lifecycle action for a workspace: its label, tone, and bound handler. */
export interface WorkspaceRowAction {
  label: string;
  variant: "secondary" | "ghost";
  perform: (workspace: WorkspaceRef) => void;
}

/**
 * The two workspace lifecycle actions, each wrapped to confirm (when destructive),
 * run via {@link runDaemonAction}, and surface a failure as a toast — the live
 * snapshot then reflects the new state, so callers need no local result store.
 * Shared by the detail page and the embedded WorkspaceRow.
 */
export function useWorkspaceActions(refetch: () => void): {
  actions: readonly WorkspaceRowAction[];
  busy: boolean;
} {
  const t = useOperatorT();
  const confirm = useConfirm();
  const toast = useToast();

  const syncBase = useOperatorAction<DaemonActionData, WorkspaceActionVars>(WORKSPACE_SYNC_BASE_MUTATION);
  const destroy = useOperatorAction<DaemonActionData, WorkspaceActionVars>(WORKSPACE_DESTROY_MUTATION);
  const busy = syncBase.result.fetching || destroy.result.fetching;

  const actions = useMemo<readonly WorkspaceRowAction[]>(() => {
    const defs = [
      { field: "workspaceSyncBase", label: t("operator.workspaces.syncBase"), variant: "secondary" as const, run: syncBase.run },
      {
        field: "workspaceDestroy",
        label: t("operator.workspaces.destroy"),
        variant: "ghost" as const,
        dangerous: true,
        run: destroy.run,
      },
    ];
    return defs.map((def) => ({
      label: def.label,
      variant: def.variant,
      perform: (workspace: WorkspaceRef) => {
        void (async () => {
          if (def.dangerous) {
            const ok = await confirm({
              title: t("operator.workspaces.destroy.confirm.title"),
              body: t("operator.workspaces.destroy.confirm.body", { name: workspace.name }),
              confirm: def.label,
              danger: true,
            });
            if (!ok) return;
          }
          await runDaemonAction({
            run: def.run,
            field: def.field,
            variables:
              def.field === "workspaceDestroy"
                ? { name: workspace.name, purge: false }
                : { name: workspace.name },
            label: def.label,
            setError: (message) => {
              if (message) toast.danger({ title: message });
            },
            refetch,
          });
        })();
      },
    }));
  }, [confirm, destroy.run, refetch, syncBase.run, t, toast]);

  return { actions, busy };
}

/** A horizontal bar of a workspace's lifecycle action buttons. */
export function WorkspaceActions({
  actions,
  busy,
  workspace,
  className = "flex justify-end gap-1",
}: {
  actions: readonly WorkspaceRowAction[];
  busy: boolean;
  workspace: WorkspaceRef;
  className?: string;
}): ReactNode {
  return (
    <div className={className}>
      {actions.map((action) => (
        <Button
          key={action.label}
          disabled={busy}
          onClick={() => action.perform(workspace)}
          size="sm"
          variant={action.variant}
        >
          {action.label}
        </Button>
      ))}
    </div>
  );
}

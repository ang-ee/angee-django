import { Button, useConfirm, useToast } from "@angee/base";
import { useMemo, type ReactNode } from "react";

import {
  WORKSPACE_DESTROY_MUTATION,
  WORKSPACE_SYNC_BASE_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
import type { WorkspaceRef } from "../../data/types";
import { runDaemonAction } from "../parts/run-action";

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

  const syncBase = useOperatorAction(WORKSPACE_SYNC_BASE_MUTATION);
  const destroy = useOperatorAction(WORKSPACE_DESTROY_MUTATION);
  const busy = syncBase.result.fetching || destroy.result.fetching;

  const actions = useMemo<readonly WorkspaceRowAction[]>(() => {
    return [
      {
        label: t("operator.workspaces.syncBase"),
        variant: "secondary",
        perform: (workspace: WorkspaceRef) => {
          void runDaemonAction({
            run: syncBase.run,
            field: "workspaceSyncBase",
            variables: { name: workspace.name },
            label: t("operator.workspaces.syncBase"),
            toast,
            refetch,
          });
        },
      },
      {
        label: t("operator.workspaces.destroy"),
        variant: "ghost",
        perform: (workspace: WorkspaceRef) => {
          void (async () => {
            const ok = await confirm({
              title: t("operator.workspaces.destroy.confirm.title"),
              body: t("operator.workspaces.destroy.confirm.body", { name: workspace.name }),
              confirm: t("operator.workspaces.destroy"),
              danger: true,
            });
            if (!ok) return;
            await runDaemonAction({
              run: destroy.run,
              field: "workspaceDestroy",
              variables: { name: workspace.name, purge: false },
              label: t("operator.workspaces.destroy"),
              toast,
              refetch,
            });
          })();
        },
      },
    ] satisfies readonly WorkspaceRowAction[];
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

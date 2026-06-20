import { useConfirm } from "@angee/base";
import { useMemo } from "react";

import {
  WORKSPACE_DESTROY_MUTATION,
  WORKSPACE_SYNC_BASE_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
import type { WorkspaceRef } from "../../data/types";
import { useRunDaemonAction } from "../parts/run-action";
import type { RowAction } from "../parts/RowActions";

/** A lifecycle action for a workspace: its label, tone, and bound handler. */
export type WorkspaceRowAction = RowAction<WorkspaceRef>;

/** Workspace lifecycle actions shared by the detail page and embedded WorkspaceRow. */
export function useWorkspaceActions(refetch: () => void): {
  actions: readonly WorkspaceRowAction[];
  busy: boolean;
} {
  const t = useOperatorT();
  const confirm = useConfirm();
  const runDaemon = useRunDaemonAction(refetch);

  const syncBase = useOperatorAction(WORKSPACE_SYNC_BASE_MUTATION);
  const destroy = useOperatorAction(WORKSPACE_DESTROY_MUTATION);
  const busy = syncBase.result.fetching || destroy.result.fetching;

  const actions = useMemo<readonly WorkspaceRowAction[]>(() => {
    return [
      {
        label: t("operator.workspaces.syncBase"),
        variant: "secondary",
        perform: (workspace: WorkspaceRef) => {
          void runDaemon({
            run: syncBase.run,
            field: "workspaceSyncBase",
            variables: { name: workspace.name },
            label: t("operator.workspaces.syncBase"),
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
            await runDaemon({
              run: destroy.run,
              field: "workspaceDestroy",
              variables: { name: workspace.name, purge: false },
              label: t("operator.workspaces.destroy"),
            });
          })();
        },
      },
    ] satisfies readonly WorkspaceRowAction[];
  }, [confirm, destroy.run, runDaemon, syncBase.run, t]);

  return { actions, busy };
}

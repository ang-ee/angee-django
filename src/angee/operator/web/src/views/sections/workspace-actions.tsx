import { useMemo } from "react";

import {
  WORKSPACE_DESTROY_MUTATION,
  WORKSPACE_SYNC_BASE_MUTATION,
} from "../../data/documents.daemon";
import { useOperatorT } from "../../i18n";
import { useOperatorAction } from "../../data/transport";
import type { WorkspaceRef } from "../../data/types";
import { useDaemonMutationRun, type DaemonVerb } from "../parts/daemon-verbs";

/** Workspace lifecycle verbs shared by the record page and the embedded WorkspaceRow. */
export function useWorkspaceActions(refetch: () => void): {
  actions: readonly DaemonVerb<WorkspaceRef>[];
  busy: boolean;
} {
  const t = useOperatorT();
  const runDaemon = useDaemonMutationRun(refetch);

  const syncBase = useOperatorAction(WORKSPACE_SYNC_BASE_MUTATION);
  const destroy = useOperatorAction(WORKSPACE_DESTROY_MUTATION);
  const busy = syncBase.result.fetching || destroy.result.fetching;

  const actions = useMemo<readonly DaemonVerb<WorkspaceRef>[]>(() => [
    {
      id: "sync-base", label: t("workspaces.syncBase"), primary: true,
      perform: (workspace) => runDaemon({ run: syncBase.run, field: "workspaceSyncBase", variables: { name: workspace.name } }),
    },
    {
      id: "destroy", label: t("workspaces.destroy"), danger: true,
      confirm: { title: t("workspaces.destroy.confirm.title"), body: (workspace) => t("workspaces.destroy.confirm.body", { name: workspace.name }) },
      perform: (workspace) => runDaemon({ run: destroy.run, field: "delete_workspaces_by_pk", variables: { id: workspace.id } }),
    },
  ], [destroy.run, runDaemon, syncBase.run, t]);

  return { actions, busy };
}

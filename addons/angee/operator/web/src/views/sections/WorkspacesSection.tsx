import { useConfirm } from "@angee/base";
import { useState, type ReactNode } from "react";

import {
  WORKSPACE_DESTROY_MUTATION,
  WORKSPACE_SYNC_BASE_MUTATION,
} from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import type { WorkspaceRef } from "../../data/types";
import { DaemonResourceTable, type DaemonResourceAction } from "../parts/DaemonResourceTable";
import { OperatorSection } from "../parts/OperatorSection";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

interface WorkspaceActionVars extends Record<string, unknown> {
  name: string;
}
interface WorkspaceAction {
  field: string;
  label: string;
  variant: "secondary" | "ghost";
  /** Destructive — require a styled confirmation naming the workspace first. */
  dangerous?: boolean;
  run: (variables: WorkspaceActionVars) => Promise<DaemonActionData>;
}

export interface WorkspacesSectionProps {
  /** Restrict the table to these workspace names; omit to show every workspace. */
  names?: readonly string[];
  /** Override the pane title (e.g. when embedded for one agent's workspace). */
  title?: string;
}

/** Workspaces pane: the daemon's worktree workspaces with sync/destroy actions. */
export function WorkspacesSection({ names, title }: WorkspacesSectionProps = {}): ReactNode {
  const t = useOperatorT();
  const confirm = useConfirm();
  const { snapshot, result, refetch } = useOperatorSnapshot({ workspaces: true });
  const [actionError, setActionError] = useState<string | null>(null);

  const syncBase = useOperatorAction<DaemonActionData, WorkspaceActionVars>(WORKSPACE_SYNC_BASE_MUTATION);
  const destroy = useOperatorAction<DaemonActionData, WorkspaceActionVars>(WORKSPACE_DESTROY_MUTATION);
  const busy = syncBase.result.fetching || destroy.result.fetching;

  const workspaces = (snapshot?.workspaces ?? []).filter(
    (workspace) => names === undefined || names.includes(workspace.name),
  );
  const actions: readonly WorkspaceAction[] = [
    { field: "workspaceSyncBase", label: t("operator.workspaces.syncBase"), variant: "secondary", run: syncBase.run },
    { field: "workspaceDestroy", label: t("operator.workspaces.destroy"), variant: "ghost", dangerous: true, run: destroy.run },
  ];

  function handle(action: WorkspaceAction, workspace: WorkspaceRef): void {
    void (async () => {
      if (action.dangerous) {
        const ok = await confirm({
          title: t("operator.workspaces.destroy.confirm.title"),
          body: t("operator.workspaces.destroy.confirm.body", { name: workspace.name }),
          confirm: action.label,
          danger: true,
        });
        if (!ok) return;
      }
      await runDaemonAction({
        run: action.run,
        field: action.field,
        variables:
          action.field === "workspaceDestroy"
            ? { name: workspace.name, purge: false }
            : { name: workspace.name },
        label: action.label,
        setError: setActionError,
        refetch,
      });
    })();
  }

  return (
    <OperatorSection
      title={title ?? t("section.operator.workspaces.title")}
      loading={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      loadingMessage={t("operator.workspaces.loading")}
      actionError={actionError}
    >
      <DaemonResourceTable
        actions={actions.map(
          (action): DaemonResourceAction<WorkspaceRef> => ({
            label: action.label,
            variant: action.variant,
            run: (workspace) => handle(action, workspace),
          }),
        )}
        actionsLabel={t("operator.table.actions")}
        busy={busy}
        columns={[
          {
            header: t("operator.workspaces.column.name"),
            cell: (workspace) => <span className="font-medium text-fg">{workspace.name}</span>,
          },
          {
            header: t("operator.workspaces.column.template"),
            cell: (workspace) => (
              <span className="text-13 text-fg-muted">{workspace.template}</span>
            ),
          },
          {
            header: t("operator.workspaces.column.path"),
            cell: (workspace) => (
              <span className="font-mono text-13 text-fg-muted">{workspace.path}</span>
            ),
          },
          {
            header: t("operator.workspaces.column.port"),
            align: "end",
            cell: (workspace) => (
              <span className="text-13 tabular-nums text-fg-muted">
                {workspace.processComposePort ?? "—"}
              </span>
            ),
          },
          {
            header: t("operator.workspaces.column.ttl"),
            cell: (workspace) => <span className="text-13 text-fg-muted">{workspace.ttl ?? "—"}</span>,
          },
        ]}
        emptyMessage={t("operator.workspaces.empty")}
        rowKey={(workspace) => workspace.name}
        rows={workspaces}
      />
    </OperatorSection>
  );
}

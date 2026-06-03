import {
  Button,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  useConfirm,
} from "@angee/base";
import { useT } from "@angee/sdk";
import { useState, type ReactNode } from "react";

import {
  WORKSPACE_DESTROY_MUTATION,
  WORKSPACE_SYNC_BASE_MUTATION,
} from "../../data/documents";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import type { WorkspaceRef } from "../../data/types";
import { SectionError, SectionLoading } from "../parts/SectionStatus";
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

/** Workspaces pane: the daemon's worktree workspaces with sync/destroy actions. */
export function WorkspacesSection(): ReactNode {
  const t = useT("operator");
  const confirm = useConfirm();
  const { snapshot, result, refetch } = useOperatorSnapshot({ workspaces: true });
  const [actionError, setActionError] = useState<string | null>(null);

  const syncBase = useOperatorAction<DaemonActionData, WorkspaceActionVars>(WORKSPACE_SYNC_BASE_MUTATION);
  const destroy = useOperatorAction<DaemonActionData, WorkspaceActionVars>(WORKSPACE_DESTROY_MUTATION);
  const busy = syncBase.result.fetching || destroy.result.fetching;

  if (result.fetching && !snapshot) {
    return <SectionLoading label="Loading workspaces" />;
  }
  if (result.error && !snapshot) {
    return <SectionError message={result.error.message} />;
  }

  const workspaces = snapshot?.workspaces ?? [];
  const actions: readonly WorkspaceAction[] = [
    { field: "workspaceSyncBase", label: "Sync base", variant: "secondary", run: syncBase.run },
    { field: "workspaceDestroy", label: "Destroy", variant: "ghost", dangerous: true, run: destroy.run },
  ];

  function handle(action: WorkspaceAction, workspace: WorkspaceRef): void {
    void (async () => {
      if (action.dangerous) {
        const ok = await confirm({
          title: "Destroy workspace?",
          body: `“${workspace.name}” will be destroyed — its files are removed and this cannot be undone.`,
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
    <div className="flex flex-col gap-4">
      <h2 className="text-lg font-semibold text-fg">{t("section.workspaces.title")}</h2>
      {actionError ? <SectionError message={actionError} /> : null}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Name</TableHead>
            <TableHead>Template</TableHead>
            <TableHead>Path</TableHead>
            <TableHead className="text-right">Port</TableHead>
            <TableHead>TTL</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {workspaces.length === 0 ? (
            <TableRow>
              <TableCell className="text-center text-13 text-fg-muted" colSpan={6}>
                No workspaces.
              </TableCell>
            </TableRow>
          ) : (
            workspaces.map((workspace) => (
              <TableRow key={workspace.name}>
                <TableCell className="font-medium text-fg">{workspace.name}</TableCell>
                <TableCell className="text-13 text-fg-muted">{workspace.template}</TableCell>
                <TableCell className="font-mono text-13 text-fg-muted">{workspace.path}</TableCell>
                <TableCell className="text-right text-13 tabular-nums text-fg-muted">
                  {workspace.processComposePort ?? "—"}
                </TableCell>
                <TableCell className="text-13 text-fg-muted">{workspace.ttl ?? "—"}</TableCell>
                <TableCell className="text-right">
                  <div className="flex justify-end gap-1">
                    {actions.map((action) => (
                      <Button
                        disabled={busy}
                        key={action.field}
                        onClick={() => handle(action, workspace)}
                        size="sm"
                        variant={action.variant}
                      >
                        {action.label}
                      </Button>
                    ))}
                  </div>
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

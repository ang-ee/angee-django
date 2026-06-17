import {
  Button,
  RowsListView,
  Skeleton,
  useConfirm,
  useToast,
  type ListColumn,
} from "@angee/base";
import { useMemo, type ReactNode } from "react";

import {
  WORKSPACE_DESTROY_MUTATION,
  WORKSPACE_SYNC_BASE_MUTATION,
} from "../../data/documents";
import { useOperatorT } from "../../i18n";
import { useOperatorAction, useOperatorSnapshot } from "../../data/transport";
import type { WorkspaceRef } from "../../data/types";
import { OperatorSection } from "../parts/OperatorSection";
import { runDaemonAction, type DaemonActionData } from "../parts/run-action";

interface WorkspaceActionVars extends Record<string, unknown> {
  name: string;
}

/** A lifecycle action rendered per workspace row: its label, tone, and handler. */
interface WorkspaceRowAction {
  label: string;
  variant: "secondary" | "ghost";
  perform: (workspace: WorkspaceRef) => void;
}

// RowsListView keys rows by `id`; the daemon identifies a workspace by name.
type WorkspaceRowData = WorkspaceRef & { id: string };

export interface WorkspacesSectionProps {
  /** Restrict the list to these workspace names; omit to show every workspace. */
  names?: readonly string[];
  /** Retained for API compatibility; the console nav owns the page heading. */
  title?: ReactNode;
}

/** Workspaces pane: the daemon's worktree workspaces with sync/destroy actions. */
export function WorkspacesSection({ names }: WorkspacesSectionProps = {}): ReactNode {
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ workspaces: true });
  const { actions, busy } = useWorkspaceActions(refetch);

  const rows = useMemo<readonly WorkspaceRowData[]>(
    () =>
      (snapshot?.workspaces ?? [])
        .filter((workspace) => names === undefined || names.includes(workspace.name))
        .map((workspace) => ({ ...workspace, id: workspace.name })),
    [names, snapshot],
  );

  const columns = useMemo<readonly ListColumn<WorkspaceRowData>[]>(
    () => [
      {
        field: "name",
        header: t("operator.workspaces.column.name"),
        render: (workspace) => <span className="font-medium text-fg">{workspace.name}</span>,
      },
      {
        field: "template",
        header: t("operator.workspaces.column.template"),
        render: (workspace) => (
          <span className="text-13 text-fg-muted">{workspace.template}</span>
        ),
      },
      {
        field: "path",
        header: t("operator.workspaces.column.path"),
        render: (workspace) => (
          <span className="font-mono text-13 text-fg-muted">{workspace.path}</span>
        ),
      },
      {
        field: "processComposePort",
        header: t("operator.workspaces.column.port"),
        align: "right",
        render: (workspace) => (
          <span className="text-13 tabular-nums text-fg-muted">
            {workspace.processComposePort ?? "—"}
          </span>
        ),
      },
      {
        field: "ttl",
        header: t("operator.workspaces.column.ttl"),
        render: (workspace) => (
          <span className="text-13 text-fg-muted">{workspace.ttl ?? "—"}</span>
        ),
      },
      {
        field: "actions",
        header: t("operator.table.actions"),
        sortable: false,
        align: "right",
        render: (workspace) => (
          <WorkspaceActions actions={actions} busy={busy} workspace={workspace} />
        ),
      },
    ],
    [actions, busy, t],
  );

  return (
    <RowsListView<WorkspaceRowData>
      rows={rows}
      columns={columns}
      fetching={result.fetching}
      error={snapshot ? null : result.error}
      emptyMessage={t("operator.workspaces.empty")}
    />
  );
}

export interface WorkspaceRowProps {
  /** The single workspace name owned by the embedding object. */
  name: string;
  /** Optional empty-state text when the daemon has not rendered the workspace yet. */
  emptyMessage?: ReactNode;
}

/** Compact single-workspace row for views that already own the workspace identity. */
export function WorkspaceRow({ name, emptyMessage }: WorkspaceRowProps): ReactNode {
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ workspaces: true });
  const { actions, busy } = useWorkspaceActions(refetch);
  const workspace =
    (snapshot?.workspaces ?? []).find((candidate) => candidate.name === name) ?? null;

  return (
    <OperatorSection
      loading={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      loadingMessage={t("operator.workspaces.loading")}
      loadingContent={<WorkspaceRowSkeleton />}
    >
      {workspace ? (
        <WorkspaceControlRow actions={actions} busy={busy} workspace={workspace} />
      ) : (
        <p className="border-y border-border-subtle py-3 text-13 text-fg-muted">
          {emptyMessage ?? t("operator.workspaces.empty")}
        </p>
      )}
    </OperatorSection>
  );
}

/**
 * The two workspace lifecycle actions, each wrapped to confirm (when destructive),
 * run via {@link runDaemonAction}, and surface a failure as a toast — the live
 * snapshot then reflects the new state, so the row needs no local result store.
 */
function useWorkspaceActions(refetch: () => void): {
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

function WorkspaceActions({
  actions,
  busy,
  workspace,
}: {
  actions: readonly WorkspaceRowAction[];
  busy: boolean;
  workspace: WorkspaceRef;
}): ReactNode {
  return (
    <div className="flex justify-end gap-1">
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

function WorkspaceControlRow({
  actions,
  busy,
  workspace,
}: {
  actions: readonly WorkspaceRowAction[];
  busy: boolean;
  workspace: WorkspaceRef;
}): ReactNode {
  return (
    <div
      className={
        "grid min-w-0 grid-cols-[minmax(0,1fr)_10rem_minmax(0,1.4fr)_max-content] " +
        "items-center gap-6 border-y border-border-subtle py-2 text-13"
      }
    >
      <span className="min-w-0 truncate font-medium text-fg">{workspace.name}</span>
      <span className="min-w-0 truncate text-fg-muted">{workspace.template}</span>
      <span className="min-w-0 truncate font-mono text-fg-muted" title={workspace.path}>
        {workspace.path}
      </span>
      <WorkspaceActions actions={actions} busy={busy} workspace={workspace} />
    </div>
  );
}

function WorkspaceRowSkeleton(): ReactNode {
  return (
    <div
      aria-hidden="true"
      className={
        "grid min-w-0 grid-cols-[minmax(0,1fr)_10rem_minmax(0,1.4fr)_max-content] " +
        "items-center gap-6 border-y border-border-subtle py-2"
      }
    >
      <Skeleton shape="text" size="sm" className="h-5" />
      <Skeleton shape="text" size="sm" className="h-5" />
      <Skeleton shape="text" size="sm" className="h-5" />
      <div className="flex shrink-0 justify-end gap-1">
        <Skeleton className="h-btn-sm w-20" />
        <Skeleton className="h-btn-sm w-16" />
      </div>
    </div>
  );
}

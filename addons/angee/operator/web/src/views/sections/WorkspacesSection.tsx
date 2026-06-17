import {
  RowsListView,
  Skeleton,
  type ListColumn,
} from "@angee/base";
import { useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import type { WorkspaceRef } from "../../data/types";
import { workspaceDetailPath } from "../../lib/paths";
import { OperatorSection } from "../parts/OperatorSection";
import {
  WorkspaceActions,
  useWorkspaceActions,
  type WorkspaceRowAction,
} from "./workspace-actions";

// RowsListView keys rows by `id`; the daemon identifies a workspace by name.
type WorkspaceRowData = WorkspaceRef & { id: string };

export interface WorkspacesSectionProps {
  /** Restrict the list to these workspace names; omit to show every workspace. */
  names?: readonly string[];
  /** Retained for API compatibility; the console nav owns the page heading. */
  title?: ReactNode;
}

/** Workspaces pane: the daemon's worktree workspaces. Rows open the detail page. */
export function WorkspacesSection({ names }: WorkspacesSectionProps = {}): ReactNode {
  const t = useOperatorT();
  const { snapshot, result } = useOperatorSnapshot({ workspaces: true });

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
    ],
    [t],
  );

  return (
    <RowsListView<WorkspaceRowData>
      rows={rows}
      columns={columns}
      rowHref={(workspace) => workspaceDetailPath(workspace.name)}
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

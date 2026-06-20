import {
  RowsListView,
  Skeleton,
  type ListColumn,
} from "@angee/base";
import { useMemo, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import type { WorkspaceRef, WorkspaceSourceStatus } from "../../data/types";
import { workspaceDetailPath } from "../../lib/paths";
import { OperatorSection } from "../parts/OperatorSection";
import { StateTag } from "../parts/StateTag";
import {
  WorkspaceActions,
  useWorkspaceActions,
  type WorkspaceRowAction,
} from "./workspace-actions";

// RowsListView keys rows by `id`; the daemon identifies a workspace by name.
type WorkspaceRowData = WorkspaceRef & { id: string };
type WorkspaceSourceRowData = WorkspaceSourceStatus & { id: string };

export interface WorkspacesSectionProps {
  /** Restrict the list to these workspace names; omit to show every workspace. */
  names?: readonly string[];
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

export interface WorkspaceSourcesProps {
  sources: readonly WorkspaceSourceStatus[];
  title?: ReactNode;
  emptyMessage?: ReactNode;
}

/** The daemon-owned source status table for a workspace. */
export function WorkspaceSources({
  sources,
  title,
  emptyMessage,
}: WorkspaceSourcesProps): ReactNode {
  const t = useOperatorT();

  const rows = useMemo<readonly WorkspaceSourceRowData[]>(
    () =>
      sources.map((source) => ({
        ...source,
        id: `${source.slot}:${source.source}`,
      })),
    [sources],
  );

  const columns = useMemo<readonly ListColumn<WorkspaceSourceRowData>[]>(
    () => [
      {
        field: "slot",
        header: t("operator.workspaceSources.column.slot"),
        render: (source) => (
          <span>
            <span className="block font-medium text-fg">{source.slot}</span>
            <span className="block text-fg-muted">{source.source}</span>
          </span>
        ),
      },
      {
        field: "state",
        header: t("operator.workspaceSources.column.state"),
        render: (source) => (
          <span>
            <StateTag state={source.state} />
            {source.error ? (
              <span className="mt-1 block text-danger-text">{source.error}</span>
            ) : null}
          </span>
        ),
      },
      {
        field: "branch",
        header: t("operator.workspaceSources.column.branch"),
        render: (source) => (
          <span className="text-13 text-fg-muted">
            {source.branch ?? source.ref ?? source.currentRef ?? "—"}
          </span>
        ),
      },
      {
        field: "drift",
        header: t("operator.workspaceSources.column.drift"),
        render: (source) => (
          <span className="text-13 text-fg-muted">{workspaceSourceDrift(source, t)}</span>
        ),
      },
      {
        field: "path",
        header: t("operator.workspaceSources.column.path"),
        render: (source) => (
          <span className="block max-w-80 truncate font-mono text-13 text-fg-muted">
            {source.path}
          </span>
        ),
      },
    ],
    [t],
  );

  return (
    <section className="flex flex-col gap-2">
      {title !== null ? (
        <h4 className="text-13 font-medium text-fg">
          {title ?? t("operator.workspaceSources.title")}
        </h4>
      ) : null}
      <RowsListView<WorkspaceSourceRowData>
        rows={rows}
        columns={columns}
        emptyMessage={emptyMessage ?? t("operator.workspaceSources.empty")}
        pageSize={5}
      />
    </section>
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

function workspaceSourceDrift(
  source: WorkspaceSourceStatus,
  t: (key: string) => string,
): string {
  if (source.error) return source.error;
  if (source.dirty) return t("operator.workspaceSources.dirty");
  const ahead = source.ahead ?? 0;
  const behind = source.behind ?? 0;
  if (ahead || behind) return `+${ahead} / -${behind}`;
  if (source.pushed === false && source.unpushedReason) return source.unpushedReason;
  return t("operator.workspaceSources.clean");
}

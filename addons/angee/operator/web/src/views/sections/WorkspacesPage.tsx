import { Button, Code, DefinitionPair, Glyph, RowsListView, SectionHeading, StatusBadge, useRouteHref, type ListColumn } from "@angee/ui";
import { ResourceQuery } from "@angee/metadata";
import { useCallback, useMemo, useState, type ReactNode } from "react";

import { useOperatorT } from "../../i18n";
import { useOperatorSnapshot } from "../../data/transport";
import { OperatorRowsList, type OperatorRowsSelector } from "../parts/operator-rows";
import type { WorkspaceRef, WorkspaceSourceStatus } from "../../data/types";
import { formatDrift } from "../../lib/drift";
import { daemonRows, daemonRowsByName, type DaemonRow } from "../parts/daemon-rows";
import { verbRowActions } from "../parts/daemon-verbs";
import { useWorkspaceActions } from "./workspace-actions";
import { WorkspaceCreateDialog } from "./WorkspaceCreateDialog";

type WorkspaceRowData = DaemonRow<WorkspaceRef>;
type WorkspaceSourceRowData = DaemonRow<WorkspaceSourceStatus>;
const workspaceSourceRowsQuery = ResourceQuery.forRows({ fields: {
  slot: { scalar: "String" }, source: { scalar: "String" }, state: { scalar: "String" },
  branch: { scalar: "String" }, path: { scalar: "String" },
} });

/** The workspace list's columns, shared by the page and the embedded single-workspace row. */
function useWorkspaceColumns(): readonly ListColumn<WorkspaceRowData>[] {
  const t = useOperatorT();
  return useMemo(() => [
    { field: "name", header: t("workspaces.column.name") },
    { field: "template", header: t("workspaces.column.template") },
    { field: "path", header: t("workspaces.column.path"), render: (workspace) => <Code truncate>{workspace.path}</Code> },
    { field: "processComposePort", header: t("workspaces.column.port"), align: "right" },
    { field: "ttl", header: t("workspaces.column.ttl") },
  ], [t]);
}

export interface WorkspacesPageProps {
  /** Restrict the list to these workspace names; omit to show every workspace. */
  names?: readonly string[];
}

/** Workspaces page: the daemon's worktree workspaces. Rows open the record page. */
export function WorkspacesPage({ names }: WorkspacesPageProps = {}): ReactNode {
  const t = useOperatorT();
  const routeHref = useRouteHref();
  const [createOpen, setCreateOpen] = useState(false);
  const selectRows = useCallback<OperatorRowsSelector<WorkspaceRowData>>(
    (snapshot) => daemonRowsByName(
      snapshot.workspaces.filter((workspace) => names === undefined || names.includes(workspace.name)),
    ),
    [names],
  );
  const columns = useWorkspaceColumns();

  return (
    <>
      <OperatorRowsList<WorkspaceRowData>
        sections={{ workspaces: true }}
        selectRows={selectRows}
        columns={columns}
        rowHref={(workspace) => routeHref("operator.workspaces.detail", { name: workspace.name })}
        toolbarActions={
          <Button type="button" variant="primary" size="sm" onClick={() => setCreateOpen(true)}>
            <Glyph decorative name="plus" />
            {t("workspaces.create.action")}
          </Button>
        }
        emptyContent={t("workspaces.empty")}
      />
      <WorkspaceCreateDialog open={createOpen} onOpenChange={setCreateOpen} />
    </>
  );
}

export interface WorkspaceRowProps {
  /** The single workspace name owned by the embedding object. */
  name: string;
  /** Optional empty-state text when the daemon has not rendered the workspace yet. */
  emptyContent?: ReactNode;
}

/** One workspace as an embedded row of the shared list, for views that already own the workspace identity. */
export function WorkspaceRow({ name, emptyContent }: WorkspaceRowProps): ReactNode {
  const t = useOperatorT();
  const { snapshot, result, refetch } = useOperatorSnapshot({ workspaces: true });
  const { actions, busy } = useWorkspaceActions(refetch);
  const columns = useWorkspaceColumns();
  const rowActions = useMemo(() => verbRowActions(actions, busy), [actions, busy]);
  const rows = useMemo(
    () => daemonRowsByName((snapshot?.workspaces ?? []).filter((candidate) => candidate.name === name)),
    [name, snapshot],
  );
  return (
    <RowsListView<WorkspaceRowData>
      presentation="embedded"
      scope="local"
      selectable={false}
      rows={rows}
      columns={columns}
      rowActions={rowActions}
      fetching={result.fetching && !snapshot}
      error={result.error && !snapshot ? result.error : null}
      emptyContent={emptyContent ?? t("workspaces.empty")}
    />
  );
}

export interface WorkspaceSourcesProps {
  sources: readonly WorkspaceSourceStatus[];
  title?: ReactNode;
  emptyContent?: ReactNode;
}

/** The daemon-owned source status table for a workspace. */
export function WorkspaceSources({ sources, title, emptyContent }: WorkspaceSourcesProps): ReactNode {
  const t = useOperatorT();
  const rows = daemonRows(sources, (source) => `${source.slot}:${source.source}`);

  const columns = useMemo<readonly ListColumn<WorkspaceSourceRowData>[]>(
    () => [
      // `sublineColumn` directly: RowsListView does not resolve `subline` for local rows (a ui gap).
      { field: "slot", header: t("workspaceSources.column.slot"), sublineColumn: { field: "source" } },
      {
        field: "state", header: t("workspaceSources.column.state"),
        render: (source) => source.error
          ? <DefinitionPair as="div" density="cell" orientation="stacked" value={<StatusBadge value={source.state} />} detail={source.error} />
          : <StatusBadge value={source.state} />,
      },
      { field: "branch", header: t("workspaceSources.column.branch"), render: (source) => source.branch ?? source.ref ?? source.currentRef },
      { field: "drift", header: t("workspaceSources.column.drift"), render: (source) => workspaceSourceDrift(source, t) },
      { field: "path", header: t("workspaceSources.column.path"), render: (source) => <Code truncate>{source.path}</Code> },
    ],
    [t],
  );

  return (
    <section className="flex flex-col gap-2">
      {title !== null ? <SectionHeading label={title ?? t("workspaceSources.title")} count={sources.length} /> : null}
      <RowsListView<WorkspaceSourceRowData>
        query={workspaceSourceRowsQuery}
        rows={rows}
        columns={columns}
        emptyContent={emptyContent ?? t("workspaceSources.empty")}
        pageSize={5}
        scope="local"
      />
    </section>
  );
}

function workspaceSourceDrift(source: WorkspaceSourceStatus, t: (key: string) => string): ReactNode {
  if (source.error) return <Code tone="danger" truncate>{source.error}</Code>;
  if (source.dirty) return <StatusBadge value="dirty" />;
  if (source.ahead || source.behind) return <span className="tabular-nums">{formatDrift(source.ahead, source.behind)}</span>;
  if (source.pushed === false && source.unpushedReason) return source.unpushedReason;
  return <StatusBadge value="clean" />;
}
